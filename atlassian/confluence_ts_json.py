#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Запускает build-meta-from-zod.ts, ждёт widget-meta.json, строит таблицу (как в макете) и обновляет Confluence.

Требования:
  - Установлен Node.js. Желательно наличие tsx (локально в node_modules/.bin или через npx) либо ts-node.

ENV для Confluence:
  CONF_URL       — базовый URL Confluence, напр.: https://confluence.company.ru
  CONF_USER      — логин/email (опционально при PAT)
  CONF_PASS      — пароль / API token / PAT (обязателен)
  CONF_PAGE_ID   — pageId страницы (опционально, по умолчанию 21609790602)

Пример запуска (Windows PowerShell, из корня проекта):
  python build_meta_to_confluence_table.py ^
    --script ".\\widget-store\\scripts\\build-meta-from-zod.ts" ^
    --outdir ".\\widget-store\\output" ^
    --outfile "widget-meta.json"
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


# -------------------- утилиты --------------------

def _which(name: str) -> Optional[str]:
    return shutil.which(name)

def _read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _detect_module_mode(project_root: Path) -> str:
    """'esm' | 'cjs' | 'unknown' по package.json/tsconfig.json."""
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

def _candidate_bins(project_root: Path) -> Dict[str, List[str]]:
    """Пути к tsx/ts-node/node/npx: локальные node_modules/.bin + системные."""
    bin_dir = project_root / "node_modules" / ".bin"
    win = os.name == "nt"

    def variants(name: str) -> List[str]:
        out: List[str] = []
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
        "tsx":   variants("tsx"),
        "tsnode": variants("ts-node"),
        "node":  variants("node") or ["node"],
        "npx":   variants("npx"),
    }

def _pick_runners(script: Path, project_root: Path) -> List[List[str]]:
    """Упорядоченный список команд запуска, с учётом ESM/CJS и наличия бинарников."""
    bins = _candidate_bins(project_root)
    mode = _detect_module_mode(project_root)

    runners: List[List[str]] = []

    # 1) TSX (локальный → npx → системный)
    for tsx in bins["tsx"]:
        runners.append([tsx, str(script)])
    if bins["npx"]:
        runners.append([bins["npx"][0], "-y", "tsx", str(script)])

    # 2) ts-node в ESM (для ESM/unknown)
    if mode in ("esm", "unknown"):
        for tsn in bins["tsnode"]:
            runners.append([tsn, "--esm", "--transpile-only", str(script)])
        runners.append([bins["node"][0], "--loader", "ts-node/esm", str(script)])

    # 3) ts-node в CJS (для CJS/unknown)
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
    print("> Запуск:", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, shell=False
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"[WinError 2] {e}")
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

def _render_agent_block(agent_obj: Any) -> str:
    """Вывести все значения из agent (без фильтра), компактно."""
    if not isinstance(agent_obj, dict):
        return "<p><strong>agent:</strong> —</p>"
    items = [f"<li><code>{_safe(k)}</code>: {_safe(v)}</li>" for k, v in agent_obj.items()]
    return f"<p><strong>agent:</strong></p><ul>{''.join(items)}</ul>"

def _storybook_link(name: str) -> str:
    # По макету: убрать подчёркивания и подставить в widget-store_widgets-{slug}--docs
    slug = name.replace("_", "")
    url = f"http://10.53.31.7:6001/public-storybook/?path=/docs/widget-store_widgets-{slug}--docs"
    return f'<a class="" href="{_safe(url)}">{_safe(url)}</a>'

def render_table_html(tools: List[Dict[str, Any]]) -> str:
    """
    Строго как в макете (table.xml): 3 колонки.
      - Виджет (name)        ← toolsMeta[].name
      - Номер дистрибутива   ← toolsMeta[].xVersion
      - Ссылка на Storybook  ← по шаблону
    """
    head = """
<table class="wrapped">
  <colgroup>
    <col/>
    <col/>
    <col/>
  </colgroup>
  <tbody>
    <tr>
      <th scope="col">Виджет (name)</th>
      <th scope="col">Номер дистрибутива (version)</th>
      <th scope="col">Ссылка на Storybook</th>
    </tr>""".rstrip()

    rows: List[str] = []
    for t in tools:
        name = str(t.get("name") or "")
        xver = t.get("xVersion")
        ver_cell = _safe(xver if xver is not None else "")
        link_cell = _storybook_link(name) if name else ""
        rows.append(
f"""
    <tr>
      <td>{_safe(name)}</td>
      <td>{ver_cell}</td>
      <td>
        {link_cell}
      </td>
    </tr>""".rstrip()
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
    p = argparse.ArgumentParser(description="Собрать таблицу из widget-meta.json и записать в Confluence")
    p.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    p.add_argument("--outdir", required=True, help="Папка output (ОТНОСИТЕЛЬНО ТЕКУЩЕЙ РАБОЧЕЙ ДИРЕКТОРИИ)")
    p.add_argument("--outfile", default="widget-meta.json", help="Имя JSON файла (по умолчанию widget-meta.json)")
    p.add_argument("--timeout", type=int, default=300, help="Таймаут выполнения TS, сек")
    p.add_argument("--wait", type=int, default=120, help="Таймаут ожидания JSON, сек")
    p.add_argument("--page-id", type=int, default=int(os.getenv("CONF_PAGE_ID", "21609790602")), help="Confluence pageId")
    args = p.parse_args()

    # --- пути ---
    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Не найден скрипт: {script}")

    # ВАЖНО: --outdir относителен текущей рабочей директории
    outdir = Path(args.outdir)
    if not outdir.is_absolute():
        outdir = Path.cwd() / outdir
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    # Корень проекта: на уровень выше папки scripts/ (или папка скрипта)
    project_root = script.parents[1] if script.parent.name.lower() in {"scripts", "script"} else script.parent
    cwd = project_root

    # --- запуск TS ---
    launched = False
    last_error = None
    for cmd in _pick_runners(script, project_root):
        try:
            _run_stream(cmd, cwd=cwd, timeout_sec=args.timeout)
            launched = True
            break
        except Exception as e:
            print(f"! Runner failed: {' '.join(cmd)}")
            print(f"  Reason: {e}")
            last_error = e
            continue
    if not launched:
        raise RuntimeError(
            "Не удалось запустить TypeScript-скрипт ни одним раннером (tsx/ts-node/node).\n"
            "Подсказка: установите один из вариантов в проекте и повторите:\n"
            "  npm i -D tsx   # рекомендуем\n"
            "  # или\n"
            "  npm i -D ts-node"
        ) from last_error

    # --- ожидание файла ---
    print("> Ожидание файла:", outfile)
    _wait_for_file(outfile, timeout_sec=args.wait)
    print(f"> Найден файл: {outfile} ({outfile.stat().st_size} bytes)")

    # --- чтение JSON ---
    try:
        with outfile.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать JSON: {e}")

    # --- подготовка данных: agent (все поля) + toolsMeta (name, xVersion) ---
    agent_obj = data.get("agent")
    tools = data.get("toolsMeta") if isinstance(data, dict) else None
    rows: List[Dict[str, Any]] = []
    if isinstance(tools, list):
        for t in tools:
            if not isinstance(t, dict):
                continue
            name = t.get("name")
            xver = t.get("xVersion")
            if name is None or xver is None:
                continue
            rows.append({"name": str(name), "xVersion": xver})

    if not rows:
        raise RuntimeError("В JSON нет toolsMeta с полями name/xVersion.")

    # --- рендер HTML согласно макету ---
    agent_block = _render_agent_block(agent_obj)
    table_html = render_table_html(rows)
    page_html = f"{agent_block}\n{table_html}"

    # --- Confluence creds ---
    conf_url = os.getenv("CONF_URL")
    conf_user = os.getenv("CONF_USER")
    conf_pass = os.getenv("CONF_PASS")
    page_id = args.page_id

    if not conf_url or not conf_pass:
        raise RuntimeError("Нужно задать переменные окружения CONF_URL и CONF_PASS (и при Basic — CONF_USER).")

    auth = _auth_header(conf_user, conf_pass)

    # --- обновление страницы ---
    page = confluence_get_page(conf_url, auth, page_id)
    title = page.get("title") or f"Page {page_id}"
    ancestors = page.get("ancestors") or []
    next_version = int(page.get("version", {}).get("number", 0)) + 1

    _ = confluence_put_storage(
        conf_url, auth, page_id,
        title, page_html, next_version,
        ancestors=ancestors,
        message="Автообновление таблицы из widget-meta.json"
    )

    print(f"✅ Обновлено: pageId={page_id} (версия {next_version}), строк: {len(rows)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
