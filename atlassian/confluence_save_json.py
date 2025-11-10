#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Запускает TS-скрипт build-meta-from-zod.ts, ждёт output/widget-meta.json,
читает JSON, формирует HTML и обновляет страницу Confluence.

Требования:
  - Node.js установлен (желательно с npm/npx)
  - Желательно наличие tsx (локально в node_modules/.bin или через npx)

Переменные окружения для Confluence:
  CONF_URL       — базовый URL Confluence, напр.: https://confluence.company.ru
  CONF_USER      — логин/email пользователя (для Basic Auth). Можно пустым, если используете PAT.
  CONF_PASS      — пароль / API token / PAT (обязателен)
  CONF_PAGE_ID   — pageId страницы (по умолчанию 21609790602)

Запуск (пример на Windows):
  python build_meta_to_confluence.py ^
    --script "C:\\path\\to\\widget-store\\scripts\\build-meta-from-zod.ts" ^
    --outdir "C:\\path\\to\\widget-store\\output" ^
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
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any


# -------------------- УТИЛИТЫ --------------------

def _which(name: str) -> Optional[str]:
    return shutil.which(name)

def _read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _detect_module_mode(project_root: Path) -> str:
    """Простой детектор ESM/CJS по package.json/tsconfig.json: 'esm' | 'cjs' | 'unknown'."""
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
    """Возвращает возможные пути к бинарям tsx/ts-node/node/npx (локальные .bin + системные)."""
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
        "tsx":   variants("tsx"),
        "tsnode": variants("ts-node"),
        "node":  variants("node") or ["node"],
        "npx":   variants("npx"),
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

    # 2) ESM-варианты ts-node — приоритетно для ESM/unknown
    if mode in ("esm", "unknown"):
        for tsn in bins["tsnode"]:
            runners.append([tsn, "--esm", "--transpile-only", str(script)])
        runners.append([bins["node"][0], "--loader", "ts-node/esm", str(script)])

    # 3) CJS-варианты ts-node — для CJS/unknown
    if mode in ("cjs", "unknown"):
        for tsn in bins["tsnode"]:
            runners.append([tsn, "--transpile-only", str(script)])
        runners.append([bins["node"][0], "-r", "ts-node/register", str(script)])

    # Удалим дубли
    uniq: List[List[str]] = []
    seen = set()
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

# -------------------- РЕНДЕР В HTML --------------------

def _safe(s: Any) -> str:
    return html.escape("" if s is None else str(s))

def _render_tools_table(tools: List[Dict[str, Any]]) -> str:
    """
    Пытаемся отрисовать таблицу по типичным полям:
      id | name | version | widgets (#) | schema fields (#)
    Скрипт устойчив к отсутствующим полям.
    """
    head = """
    <tr>
      <th>#</th>
      <th>Tool ID</th>
      <th>Name</th>
      <th>Version</th>
      <th>Widgets</th>
      <th>Schema fields</th>
    </tr>
    """.strip()

    rows = [head]
    for i, t in enumerate(tools, 1):
        tool_id = t.get("id") or t.get("toolId") or t.get("key") or ""
        name = t.get("name") or t.get("title") or ""
        version = t.get("version") or t.get("ver") or ""
        widgets = t.get("widgets") or t.get("selectedWidgets") or t.get("modules") or []
        schema = t.get("schema") or t.get("zodSchema") or t.get("jsonSchema") or {}
        widgets_count = "-"
        if isinstance(widgets, list):
            widgets_count = str(len(widgets))
        schema_fields = "-"
        if isinstance(schema, dict):
            schema_fields = str(len(schema.get("properties", {}))) if "properties" in schema else str(len(schema))

        rows.append(f"""
        <tr>
          <td>{i}</td>
          <td><code>{_safe(tool_id)}</code></td>
          <td>{_safe(name)}</td>
          <td>{_safe(version)}</td>
          <td style="text-align:center">{_safe(widgets_count)}</td>
          <td style="text-align:center">{_safe(schema_fields)}</td>
        </tr>
        """.strip())

    return f"""
    <table data-layout="wide">
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    """.strip()

def _render_html_from_json(data: Dict[str, Any]) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts: List[str] = [f'<p><strong>Widget meta</strong> — обновлено: {_safe(ts)}</p>']

    # Попробуем найти массив с метаданными инструментов
    tools = None
    if isinstance(data.get("toolsMeta"), list):
        tools = data["toolsMeta"]
    elif isinstance(data.get("tools"), list):
        tools = data["tools"]
    elif isinstance(data.get("items"), list):
        tools = data["items"]

    # Краткая сводка
    total_tools = len(tools) if isinstance(tools, list) else "—"
    parts.append(f"<p>Всего инструментов: <strong>{_safe(total_tools)}</strong></p>")

    # Таблица по toolsMeta (если нашли)
    if isinstance(tools, list) and tools:
        parts.append(_render_tools_table(tools))

    # Блок с «сырым» JSON (сверху — таблица, снизу — raw)
    raw_json = html.escape(json.dumps(data, ensure_ascii=False, indent=2))
    parts.append(f"""
    <details>
      <summary>Показать исходный JSON</summary>
      <pre>{raw_json}</pre>
    </details>
    """.strip())

    return "\n".join(parts)


# -------------------- CONFLUENCE REST --------------------

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
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {e.read().decode('utf-8','ignore')}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"HTTP error: {e}") from None

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


# -------------------- MAIN --------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Запуск build-meta-from-zod.ts, чтение widget-meta.json и обновление Confluence")
    ap.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    ap.add_argument("--outdir", required=True, help="Папка output (куда пишет TS)")
    ap.add_argument("--outfile", default="widget-meta.json", help="Имя файла результата (по умолчанию widget-meta.json)")
    ap.add_argument("--timeout", type=int, default=300, help="Таймаут выполнения TS-скрипта, сек (default 300)")
    ap.add_argument("--wait", type=int, default=120, help="Таймаут ожидания файла результата, сек (default 120)")
    ap.add_argument("--page-id", type=int, default=int(os.getenv("CONF_PAGE_ID", "21609790602")), help="Confluence pageId (по умолчанию из CONF_PAGE_ID или 21609790602)")
    args = ap.parse_args()

    # --- пути и корень проекта ---
    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Не найден скрипт: {script}")
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    project_root = script.parents[1] if script.parent.name.lower() in {"scripts", "script"} else script.parent
    cwd = project_root

    # --- запуск TS ---
    for cmd in _pick_runners(script, project_root):
        try:
            _run_stream(cmd, cwd=cwd, timeout_sec=args.timeout)
            break
        except Exception as e:
            print(f"! Runner failed: {' '.join(cmd)}")
            print(f"  Reason: {e}")
            continue
    else:
        raise RuntimeError("Не удалось запустить TypeScript-скрипт ни одним раннером (tsx/ts-node/node).")

    # --- ожидание файла ---
    print("> Ожидание файла:", outfile)
    _wait_for_file(outfile, timeout_sec=args.wait)
    print(f"> Найден файл: {outfile} ({outfile.stat().st_size} bytes)")

    # --- читаем JSON и готовим HTML ---
    try:
        with outfile.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать JSON из {outfile}: {e}")

    if not isinstance(data, dict):
        # На всякий случай завернём не-объект в объект
        data = {"data": data}

    html_body = _render_html_from_json(data)

    # --- Confluence creds ---
    conf_url = os.getenv("CONF_URL")
    conf_user = os.getenv("CONF_USER")
    conf_pass = os.getenv("CONF_PASS")
    page_id = args.page_id

    if not conf_url or not conf_pass:
        raise RuntimeError("Задайте переменные окружения CONF_URL и CONF_PASS (и при Basic — CONF_USER).")

    auth = _auth_header(conf_user, conf_pass)

    # --- читаем текущую страницу и пишем новую версию ---
    page = confluence_get_page(conf_url, auth, page_id)
    title = page.get("title") or f"Page {page_id}"
    ancestors = page.get("ancestors") or []
    next_version = int(page.get("version", {}).get("number", 0)) + 1

    _ = confluence_put_storage(
        conf_url, auth, page_id,
        title, html_body, next_version,
        ancestors=ancestors,
        message="Автообновление: widget-meta.json"
    )

    print(f"✅ Обновлено: pageId={page_id} (версия {next_version})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
