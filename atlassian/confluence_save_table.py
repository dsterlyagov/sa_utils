#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Генерирует таблицу для Confluence из widget-meta.json и обновляет страницу,
не меняя иерархию (НЕ отправляем ancestors в PUT).

Колонки:
 - name
 - xVersion
 - agents          (массив -> строка через ", ")
 - display_description
 - Storybook (ссылка)

Запуск примеры (Windows):
  python build_meta_to_confluence_table.py ^
    --script ".\\widget-store\\scripts\\build-meta-from-zod.ts" ^
    --outdir ".\\widget-store\\output" ^
    --outfile "widget-meta.json" ^
    --page-id 21609790602
Переменные окружения (обязательно):
  CONF_URL, CONF_PASS; при Basic ещё CONF_USER
"""

import argparse
import base64
import html
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# -------------------- запуск TS --------------------

def _which(name: str) -> Optional[str]:
    return shutil.which(name)

def _read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _detect_module_mode(project_root: Path) -> str:
    pkg = _read_json(project_root / "package.json") or {}
    if pkg.get("type") == "module":
        return "esm"
    tc = _read_json(project_root / "tsconfig.json") or {}
    comp = (tc.get("compilerOptions") or {})
    module = (comp.get("module") or "").lower()
    if module.startswith("es"):
        return "esm"
    if module in {"commonjs", "cjs"}:
        return "cjs"
    return "unknown"

def _candidate_bins(project_root: Path) -> dict:
    bin_dir = project_root / "node_modules" / ".bin"
    win = os.name == "nt"

    def variants(name: str) -> List[str]:
        out = []
        if win:
            p = (bin_dir / f"{name}.cmd").resolve()
            if p.exists():
                out.append(str(p))
        p2 = (bin_dir / name).resolve()
        if p2.exists():
            out.append(str(p2))
        sysbin = _which(name)
        if sysbin:
            out.append(sysbin)
        return out

    return {
        "tsx": variants("tsx"),
        "tsnode": variants("ts-node"),
        "node": variants("node") or ["node"],
        "npx": variants("npx"),
    }

def _pick_runners(script: Path, project_root: Path) -> List[List[str]]:
    bins = _candidate_bins(project_root)
    mode = _detect_module_mode(project_root)

    runners: List[List[str]] = []

    # 1) TSX (локальный → npx → системный)
    for tsx in bins["tsx"]:
        runners.append([tsx, str(script)])
    if bins["npx"]:
        runners.append([bins["npx"][0], "-y", "tsx", str(script)])

    # 2) ts-node ESM
    if mode in ("esm", "unknown"):
        for tsn in bins["tsnode"]:
            runners.append([tsn, "--esm", "--transpile-only", str(script)])
        runners.append([bins["node"][0], "--loader", "ts-node/esm", str(script)])

    # 3) ts-node CJS
    if mode in ("cjs", "unknown"):
        for tsn in bins["tsnode"]:
            runners.append([tsn, "--transpile-only", str(script)])
        runners.append([bins["node"][0], "-r", "ts-node/register", str(script)])

    # Уникализация по строке
    uniq, seen = [], set()
    for r in runners:
        key = " ".join(r)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq

def _run_stream(cmd: List[str], cwd: Path, timeout_sec: int) -> None:
    print("> Запуск:", " ".join(cmd))
    proc = subprocess.Popen(
        cmd, cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, shell=False
    )
    start = time.time()
    try:
        while True:
            if proc.poll() is not None:
                break
            if time.time() - start > timeout_sec:
                proc.kill()
                raise TimeoutError(f"Превышен таймаут {timeout_sec} сек")
            line = proc.stdout.readline()  # type: ignore
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                time.sleep(0.05)
        tail = proc.stdout.read() if proc.stdout else ""  # type: ignore
        if tail:
            sys.stdout.write(tail)
        if proc.returncode != 0:
            raise RuntimeError(f"Процесс завершился с кодом {proc.returncode}")
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass

def _wait_for_file(path: Path, timeout_sec: int) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.2)
    raise TimeoutError(f"Файл не появился за {timeout_sec} сек: {path}")

# -------------------- рендер таблицы --------------------

def _safe(v: Any) -> str:
    return html.escape("" if v is None else str(v))

def _story_link(name: str) -> str:
    # Удаляем подчёркивания как раньше
    slug = (name or "").replace("_", "")
    url = f"http://10.53.31.7:6001/public-storybook/?path=/docs/widget-store_widgets-{slug}--docs"
    return f'<a href="{_safe(url)}">{_safe(url)}</a>'

def render_table_html(rows: List[Dict[str, Any]]) -> str:
    head = """
<table class="wrapped">
  <colgroup><col/><col/><col/><col/><col/></colgroup>
  <tbody>
    <tr>
      <th scope="col">name</th>
      <th scope="col">xVersion</th>
      <th scope="col">agents</th>
      <th scope="col">display_description</th>
      <th scope="col">Storybook</th>
    </tr>
""".rstrip()

    body_rows: List[str] = []
    for r in rows:
        name = str(r.get("name") or "")
        xver = r.get("xVersion")
        agents = r.get("agents")
        descr = r.get("display_description")

        if isinstance(agents, list):
            agents_cell = _safe(", ".join(map(str, agents)))
        else:
            agents_cell = _safe("" if agents is None else agents)

        body_rows.append(
f"""    <tr>
      <td>{_safe(name)}</td>
      <td>{_safe("" if xver is None else xver)}</td>
      <td>{agents_cell}</td>
      <td>{_safe(descr)}</td>
      <td>{_story_link(name)}</td>
    </tr>"""
        )

    tail = """
  </tbody>
</table>
""".lstrip()

    return "\n".join([head] + body_rows + [tail])

# -------------------- Confluence REST --------------------

def _auth_header(user: Optional[str], pwd: str) -> str:
    raw = f"{user or ''}:{pwd}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")

def _http(method: str, url: str, headers: Dict[str, str], data: Optional[bytes] = None) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            if not body:
                return {}
            try:
                return json.loads(body.decode("utf-8"))
            except Exception:
                return {"_raw": body.decode("utf-8", "ignore")}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {e.read().decode('utf-8','ignore')}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"HTTP error: {e}")

def confluence_get_page(conf_url: str, auth: str, page_id: int) -> Dict[str, Any]:
    url = f"{conf_url}/rest/api/content/{page_id}?expand=version"
    headers = {"Accept": "application/json", "Authorization": auth}
    return _http("GET", url, headers)

def confluence_put_storage_preserve_parent(conf_url: str, auth: str, page_id: int, title: str, html_body: str, next_version: int, message: str = "") -> Dict[str, Any]:
    """
    ВАЖНО: ancestors НЕ передаём -> родитель остаётся прежним, иерархия не ломается.
    """
    url = f"{conf_url}/rest/api/content/{page_id}"
    payload = {
        "id": str(page_id),
        "type": "page",
        "title": title,
        "version": {"number": next_version, "minorEdit": True, "message": message or ""},
        "body": {"storage": {"representation": "storage", "value": html_body}}
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": auth
    }
    return _http("PUT", url, headers, data=data)

# -------------------- main --------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Собрать таблицу из widget-meta.json и записать в Confluence (без смены родителя)")
    ap.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    ap.add_argument("--outdir", required=True, help="Папка output (относительно ТЕКУЩЕЙ рабочей директории; будет создана при необходимости)")
    ap.add_argument("--outfile", default="widget-meta.json", help="Имя JSON файла (по умолчанию widget-meta.json)")
    ap.add_argument("--timeout", type=int, default=300, help="Таймаут выполнения TS, сек")
    ap.add_argument("--wait", type=int, default=120, help="Таймаут ожидания JSON, сек")
    ap.add_argument("--page-id", type=int, required=True, help="Confluence pageId")
    args = ap.parse_args()

    # --- пути ---
    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Не найден скрипт: {script}")

    # outdir — ОТНОСИТЕЛЬНО ТЕКУЩЕЙ РАБОЧЕЙ ДИРЕКТОРИИ (cwd)
    outdir = Path(args.outdir)
    if not outdir.is_absolute():
        outdir = Path.cwd() / outdir
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    # предполагаемый корень проекта (важно для относительных импортов в TS)
    project_root = script.parents[1] if script.parent.name.lower() in {"scripts", "script"} else script.parent
    cwd = project_root

    # --- запуск TS ---
    runners = _pick_runners(script, project_root)
    last_err = None
    for cmd in runners:
        try:
            _run_stream(cmd, cwd=cwd, timeout_sec=args.timeout)
            last_err = None
            break
        except Exception as e:
            print(f"! Runner failed: {' '.join(cmd)}")
            print(f"  Reason: {e}")
            last_err = e
            continue
    if last_err:
        raise RuntimeError("Не удалось запустить TypeScript-скрипт ни одним раннером (tsx/ts-node/node).")

    # --- ожидание файла ---
    print("> Ожидание файла:", outfile)
    _wait_for_file(outfile, timeout_sec=args.wait)
    print(f"> Найден файл: {outfile} ({outfile.stat().st_size} bytes)")

    # --- читаем JSON и готовим таблицу ---
    try:
        with outfile.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать JSON: {e}")

    # источник данных: toolsMeta на верхнем уровне. Поддержим agents/description, если встречаются в элементах.
    tools = data.get("toolsMeta")
    if not isinstance(tools, list) or not tools:
        raise RuntimeError("В JSON отсутствует непустой массив 'toolsMeta'.")

    rows: List[Dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        xver = t.get("xVersion")
        agents = t.get("agents")  # ожидается list[str] или str
        descr = t.get("display_description")
        if name is None:
            continue
        rows.append({
            "name": name,
            "xVersion": xver,
            "agents": agents,
            "display_description": descr
        })

    table_html = render_table_html(rows)

    # --- Confluence creds ---
    conf_url = os.getenv("CONF_URL")
    conf_user = os.getenv("CONF_USER")
    conf_pass = os.getenv("CONF_PASS")
    if not conf_url or not conf_pass:
        raise RuntimeError("Задайте переменные окружения CONF_URL и CONF_PASS (и при Basic — CONF_USER).")
    auth = _auth_header(conf_user, conf_pass)

    # --- читаем страницу (чтобы взять title и версию) ---
    page = confluence_get_page(conf_url, auth, args.page_id)
    title = page.get("title") or f"Page {args.page_id}"
    next_version = int(page.get("version", {}).get("number", 0)) + 1

    # --- важное: НЕ передаём ancestors, чтобы не трогать родителя ---
    _ = confluence_put_storage_preserve_parent(
        conf_url, auth, args.page_id,
        title, table_html, next_version,
        message="Автообновление таблицы (name, xVersion, agents, display_description, Storybook)"
    )

    print(f"✅ Обновлено: pageId={args.page_id} (версия {next_version})")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
