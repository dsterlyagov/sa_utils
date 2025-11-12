#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Запускает build-meta-from-zod.ts (если доступен раннер), читает JSON из --outdir/--outfile,
строит таблицу (name, xVersion, agents, display_description, Storybook) и записывает в Confluence.

ENV:
  CONF_URL       — https://confluence.example.com
  CONF_USER      — логин/email (опционально, если используете PAT)
  CONF_PASS      — пароль / API токен / PAT (обязательно)
  CONF_PAGE_ID   — pageId (опционально; можно передать флагом --page-id)

Пример запуска:
  python build_meta_to_confluence_table.py ^
    --script .\widget-store\scripts\build-meta-from-zod.ts ^
    --outdir .\widget-store\output ^
    --outfile widget-meta.json ^
    --page-id 21609790602
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

def _read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _detect_module_mode(project_root: Path) -> str:
    """esm | cjs | unknown"""
    pkg = _read_json(project_root / "package.json") or {}
    if pkg.get("type") == "module":
        return "esm"
    tsconfig = _read_json(project_root / "tsconfig.json") or {}
    comp = (tsconfig.get("compilerOptions") or {})
    module = (comp.get("module") or "").lower()
    if module.startswith("es"):
        return "esm"
    if module in {"commonjs", "cjs"}:
        return "cjs"
    return "unknown"

def _candidate_bins(project_root: Path) -> dict:
    """пути к бинарям из node_modules/.bin + системные"""
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
        sysbin = shutil.which(name)
        if sysbin:
            out.append(sysbin)
        return out

    return {
        "tsx":   variants("tsx"),
        "tsnode": variants("ts-node"),
        "node":  variants("node") or ["node"],
        "npx":   variants("npx"),
    }

def _pick_runners(script: Path, project_root: Path) -> List[List[str]]:
    """возвращает список команд (argv) по убыванию приоритета"""
    bins = _candidate_bins(project_root)
    mode = _detect_module_mode(project_root)

    runners: List[List[str]] = []

    # 1) TSX (локальный — приоритетней; затем npx; затем системный)
    for tsx in bins["tsx"]:
        runners.append([tsx, str(script)])
    if bins["npx"]:
        runners.append([bins["npx"][0], "-y", "tsx", str(script)])

    # 2) ESM-варианты ts-node
    if mode in ("esm", "unknown"):
        for tsn in bins["tsnode"]:
            runners.append([tsn, "--esm", "--transpile-only", str(script)])
        runners.append([bins["node"][0], "--loader", "ts-node/esm", str(script)])

    # 3) CJS-варианты ts-node
    if mode in ("cjs", "unknown"):
        for tsn in bins["tsnode"]:
            runners.append([tsn, "--transpile-only", str(script)])
        runners.append([bins["node"][0], "-r", "ts-node/register", str(script)])

    # Уникализируем
    uniq, seen = [], set()
    for r in runners:
        key = " ".join(r)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq

def _run_stream(cmd: List[str], cwd: Path, timeout_sec: int) -> None:
    """стримим stdout/stderr в кодировке UTF-8 с игнором ошибок, чтобы избежать 'charmap'"""
    print("> Запуск:", " ".join(cmd))
    proc = subprocess.Popen(
        cmd, cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False
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

def _maybe_run_ts(script: Path, project_root: Path, timeout: int) -> None:
    """
    Пытаемся запустить TS. Если нет ни одного рабочего раннера — просто сообщаем и продолжаем:
    будем читать уже существующий widget-meta.json.
    """
    for cmd in _pick_runners(script, project_root):
        try:
            _run_stream(cmd, cwd=project_root, timeout_sec=timeout)
            return
        except Exception as e:
            print(f"! Runner failed: {' '.join(cmd)}")
            print(f"  Reason: {e}")
            continue
    print("! Не удалось запустить TS-скрипт ни одним раннером — пропускаю запуск и использую существующий JSON.")

# -------------------- таблица и маппинг полей --------------------

def _safe(v: Any) -> str:
    return html.escape("" if v is None else str(v))

def _extract_display_description(item: Dict[str, Any]) -> str:
    # 1) xMeta.display_description
    xmeta = item.get("xMeta")
    if isinstance(xmeta, dict) and isinstance(xmeta.get("display_description"), str):
        return xmeta["display_description"]

    # 2) xPayload (строка-JSON)
    xpayload = item.get("xPayload")
    if isinstance(xpayload, str):
        try:
            obj = json.loads(xpayload)
            if isinstance(obj, dict) and isinstance(obj.get("display_description"), str):
                return obj["display_description"]
        except Exception:
            pass

    # 3) пусто
    return ""

def _agents_list(item: Dict[str, Any]) -> str:
    agents = item.get("agents")
    if not isinstance(agents, list):
        return ""
    names = []
    for a in agents:
        if isinstance(a, dict):
            n = a.get("name")
            if n:
                names.append(str(n))
    return ", ".join(names)

def _storybook_link(name: str) -> str:
    # как ранее: убираем underscore и подставляем в path
    slug = (name or "").replace("_", "")
    url = f"http://10.53.31.7:6001/public-storybook/?path=/docs/widget-store_widgets-{slug}--docs"
    return f'<a href="{_safe(url)}">{_safe(url)}</a>'

def render_table_html(items: List[Dict[str, Any]]) -> str:
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

    rows: List[str] = []
    for it in items:
        name = str(it.get("name") or "")
        xver = it.get("xVersion")
        agents = _agents_list(it)
        desc = _extract_display_description(it)
        link = _storybook_link(name) if name else ""

        rows.append(
f"""    <tr>
      <td>{_safe(name)}</td>
      <td>{_safe(xver if xver is not None else "")}</td>
      <td>{_safe(agents)}</td>
      <td>{_safe(desc)}</td>
      <td>{link}</td>
    </tr>"""
        )

    tail = """
  </tbody>
</table>
""".lstrip()

    return "\n".join([head] + rows + [tail])

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
    url = f"{conf_url}/rest/api/content/{page_id}?expand=version,ancestors"
    headers = {"Accept": "application/json", "Authorization": auth}
    return _http("GET", url, headers)

def _parent_ancestor(ancestors):
    """Return a minimal ancestors payload for updates (only the direct parent).
    Passing the full ancestors chain when updating can reparent/move the page in some Confluence setups.
    If no ancestors are provided, return None so we don't touch parentage at all.
    """
    try:
        if ancestors and isinstance(ancestors, (list, tuple)):
            last = ancestors[-1]
            pid = str(last.get("id") or last.get("content", {}).get("id"))
            if pid:
                return [{"id": pid}]
    except Exception:
        pass
    return None

def confluence_put_storage(conf_url: str, auth: str, page_id: int, title: str, html_body: str, next_version: int, ancestors=None, message: str = "") -> Dict[str, Any]:
    url = f"{conf_url}/rest/api/content/{page_id}"
    payload = {
        "id": str(page_id),
        "type": "page",
        "title": title,
        "version": {"number": next_version, "minorEdit": True, "message": message or ""},
        "body": {"storage": {"representation": "storage", "value": html_body}}
    }
    if ancestors:
        payload["ancestors"] = ancestors
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": auth
    }
    return _http("PUT", url, headers, data=data)

# -------------------- main --------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Собрать таблицу из widget-meta.json и записать в Confluence")
    ap.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    ap.add_argument("--outdir", required=True, help="Папка вывода (относительно ТЕКУЩЕЙ рабочей директории)")
    ap.add_argument("--outfile", default="widget-meta.json", help="Имя JSON файла (default: widget-meta.json)")
    ap.add_argument("--timeout", type=int, default=300, help="Таймаут запуска TS, сек (default 300)")
    ap.add_argument("--wait", type=int, default=120, help="Таймаут ожидания JSON, сек (default 120)")
    ap.add_argument("--page-id", type=int, default=int(os.getenv("CONF_PAGE_ID", "21609790602")), help="Confluence pageId")
    args = ap.parse_args()

    # --- пути ---
    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Не найден скрипт: {script}")

    # ВАЖНО: --outdir интерпретируем относительно ТЕКУЩЕЙ РАБОЧЕЙ ДИРЕКТОРИИ (os.getcwd())
    outdir = (Path(os.getcwd()) / args.outdir).resolve() if not Path(args.outdir).is_absolute() else Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    # предполагаемый корень проекта: на уровень выше папки scripts/
    project_root = script.parents[1] if script.parent.name.lower() in {"scripts", "script"} else script.parent

    # --- запускаем TS (если есть раннеры) ---
    _maybe_run_ts(script, project_root, timeout=args.timeout)

    # --- читаем JSON ---
    print("> Ожидание файла:", outfile)
    t0, wait_sec = time.time(), args.wait
    while time.time() - t0 < wait_sec:
        if outfile.exists() and outfile.stat().st_size > 0:
            break
        time.sleep(0.2)
    if not outfile.exists():
        raise RuntimeError(f"Файл не появился за {wait_sec} сек: {outfile}")
    print(f"> Найден файл: {outfile} ({outfile.stat().st_size} bytes)")

    try:
        with outfile.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать JSON: {e}")

    if not isinstance(data, list):
        raise RuntimeError("Ожидался массив объектов с виджетами.")

    # --- строим таблицу ---
    table_html = render_table_html(data)
    page_html = f"""
<p><strong>Widget meta table</strong> — обновлено: {html.escape(time.strftime('%Y-%m-%d %H:%M:%S'))}</p>
{table_html}
""".strip()

    # --- отправляем в Confluence ---
    conf_url = 'https://confluence.sberbank.ru'
    conf_user = '19060455'
    conf_pass = '19#MOSiad#24'
    page_id = args.page_id
    if not conf_url or not conf_pass:
        raise RuntimeError("Нужно задать ENV: CONF_URL и CONF_PASS (и при Basic — CONF_USER).")

    auth = _auth_header(conf_user, conf_pass)
    page = confluence_get_page(conf_url, auth, page_id)
    title = page.get("title") or f"Page {page_id}"
    ancestors = page.get("ancestors") or []
    next_version = int(page.get("version", {}).get("number", 0)) + 1

    _ = confluence_put_storage(
        conf_url, auth, page_id,
        title, page_html, next_version,
        ancestors=_parent_ancestor(ancestors),
        message="Автообновление: таблица (name, xVersion, agents, display_description, Storybook)"
    )
    print(f"✅ Обновлено: pageId={page_id} (версия {next_version})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
