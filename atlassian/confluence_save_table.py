#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
1) Запускает TS-скрипт build-meta-from-zod.ts безопасно для Windows:
   - приоритет: локальные бинарники: node_modules/.bin/tsx(.cmd) -> ts-node(.cmd)
   - далее: node --loader ts-node/esm (только если локальный ts-node установлен)
   - НИКАКОГО npx (часто блокируется корпоративным Nexus и сыплет E401)
   - фиксим 'charmap' вывод: читаем stdout/stderr в UTF-8 с errors='ignore'

2) Работает с outdir ОТНОСИТЕЛЬНО ТЕКУЩЕЙ РАБОЧЕЙ ДИРЕКТОРИИ (CWD).
   - По умолчанию ./output
   - Папка создаётся, если её нет.

3) Читает ./output/widget-meta.json, строит таблицу:
   columns: name | xVersion | agents | display_description | Storybook

   agents:
     - если у тулза есть поле "agents": превращаем в строку "a, b, c"
     - иначе берём все доступные значения из верхнего "agent"
       (если объект — name, если список — join по name/строке)

   Storybook:
     http://10.53.31.7:6001/public-storybook/?path=/docs/widget-store_widgets-{name_without_underscores}--docs

4) Публикует таблицу на страницу Confluence (storage), НЕ ПЕРЕДАВАЯ ancestors,
   чтобы не ломать иерархию.

ENV (или задайте флагами):
  CONF_URL   — https://confluence.company.ru
  CONF_USER  — логин/email (можно пустым при PAT, тогда просто ":TOKEN")
  CONF_PASS  — пароль или PAT (обязательно)
  CONF_PAGE_ID — pageId (по умолчанию 21609790602)
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

def _bin_in_project(project_root: Path, name: str) -> List[str]:
    """
    Возвращает список путей к локальным бинарникам в node_modules/.bin
    Учитываем Windows (.cmd).
    """
    out: List[str] = []
    bin_dir = project_root / "node_modules" / ".bin"
    if os.name == "nt":
        p = (bin_dir / f"{name}.cmd").resolve()
        if p.exists():
            out.append(str(p))
    p2 = (bin_dir / name).resolve()
    if p2.exists():
        out.append(str(p2))
    return out

def _detect_mode(project_root: Path) -> str:
    """esm | cjs | unknown по package.json/tsconfig.json"""
    def _read_json(p: Path) -> Optional[dict]:
        try:
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    pkg = _read_json(project_root / "package.json") or {}
    if pkg.get("type") == "module":
        return "esm"
    tsconfig = _read_json(project_root / "tsconfig.json") or {}
    comp = (tsconfig.get("compilerOptions") or {})
    mod = (comp.get("module") or "").lower()
    if mod.startswith("es"):
        return "esm"
    if mod in {"commonjs", "cjs"}:
        return "cjs"
    return "unknown"

def run_ts(script: Path, timeout_sec: int) -> None:
    """
    Пытаемся запустить TS без npx:
      1) <project>/node_modules/.bin/tsx(.cmd) <script>
      2) <project>/node_modules/.bin/ts-node(.cmd) --esm --transpile-only <script> (если ESM/unknown)
      3) node --loader ts-node/esm <script> (только если локальный ts-node найден)
      4) <project>/node_modules/.bin/ts-node(.cmd) --transpile-only <script> (для CJS/unknown)

    Вывод читаем в UTF-8, errors='ignore' (устраняет 'charmap' codec ...).
    """
    # project_root — на уровень выше папки scripts/
    project_root = script.parents[1] if script.parent.name.lower() in {"scripts", "script"} else script.parent
    cwd = project_root

    mode = _detect_mode(project_root)
    tsx_bins = _bin_in_project(project_root, "tsx")
    tsnode_bins = _bin_in_project(project_root, "ts-node")

    candidates: List[List[str]] = []

    # 1) TSX локальный
    for tsx in tsx_bins:
        candidates.append([tsx, str(script)])

    # 2) ts-node ESM (если режим ESM или неизвестен)
    if mode in ("esm", "unknown"):
        for tsn in tsnode_bins:
            candidates.append([tsn, "--esm", "--transpile-only", str(script)])
        # node --loader ts-node/esm — только если есть локальный ts-node
        if tsnode_bins:
            node_exe = shutil.which("node") or "node"
            candidates.append([node_exe, "--loader", "ts-node/esm", str(script)])

    # 3) ts-node CJS (для CJS/unknown)
    if mode in ("cjs", "unknown"):
        for tsn in tsnode_bins:
            candidates.append([tsn, "--transpile-only", str(script)])

    # Последняя страховка: если вообще ничего нет — сообщим явно
    if not candidates:
        raise RuntimeError(
            "Не найден ни tsx, ни ts-node в локальном node_modules/.bin.\n"
            "Установите один из них в проект:\n"
            "  npm i -D tsx   (предпочтительно)\n"
            "или\n"
            "  npm i -D ts-node\n"
        )

    last_err = None
    for cmd in candidates:
        try:
            print("> Запуск:", " ".join(cmd))
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                shell=False
            )
            start = time.time()
            while True:
                if proc.poll() is not None:
                    break
                if time.time() - start > timeout_sec:
                    proc.kill()
                    raise TimeoutError(f"Превышен таймаут {timeout_sec} сек")
                line = proc.stdout.readline() if proc.stdout else ""
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    time.sleep(0.05)
            tail = proc.stdout.read() if proc.stdout else ""
            if tail:
                sys.stdout.write(tail)
            if proc.returncode != 0:
                raise RuntimeError(f"Процесс завершился с кодом {proc.returncode}")
            return
        except Exception as e:
            print(f"! Runner failed: {' '.join(cmd)}")
            print(f"  Reason: {e}")
            last_err = e
            continue
    raise RuntimeError(f"Не удалось запустить TypeScript-скрипт ни одним способом. Последняя ошибка: {last_err}")

# -------------------- JSON и рендер --------------------

def _safe(v: Any) -> str:
    return html.escape("" if v is None else str(v))

def storybook_link(name: str) -> str:
    slug = (name or "").replace("_", "")
    url = f"http://10.53.31.7:6001/public-storybook/?path=/docs/widget-store_widgets-{slug}--docs"
    return f'<a href="{_safe(url)}">{_safe(url)}</a>'

def extract_agents_for_row(root: Dict[str, Any], tool: Dict[str, Any]) -> str:
    """
    Возвращает строку для колонки 'agents'.
    Приоритет:
      1) tool["agents"] (список/строка)
      2) root["agent"] (если dict -> name; если список -> join по name/строке; если строка -> она)
    """
    # 1) из тулза
    a = tool.get("agents")
    if isinstance(a, list):
        vals = []
        for x in a:
            if isinstance(x, dict) and "name" in x:
                vals.append(str(x["name"]))
            else:
                vals.append(str(x))
        if vals:
            return ", ".join(vals)
    elif isinstance(a, dict):
        if "name" in a:
            return str(a["name"])
    elif isinstance(a, str):
        if a.strip():
            return a.strip()

    # 2) из корня
    root_agent = root.get("agent")
    if isinstance(root_agent, dict):
        n = root_agent.get("name")
        if n:
            return str(n)
    elif isinstance(root_agent, list):
        vals = []
        for x in root_agent:
            if isinstance(x, dict) and "name" in x:
                vals.append(str(x["name"]))
            else:
                vals.append(str(x))
        if vals:
            return ", ".join(vals)
    elif isinstance(root_agent, str):
        if root_agent.strip():
            return root_agent.strip()
    return ""

def build_table_html(root: Dict[str, Any]) -> str:
    """
    Формируем таблицу Confluence (storage):
      name | xVersion | agents | display_description | Storybook
    Берём данные из root["toolsMeta"] (список объектов).
    """
    tools = root.get("toolsMeta")
    if not isinstance(tools, list) or not tools:
        raise RuntimeError("В JSON не найден непустой список toolsMeta")

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
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or "")
        xver = t.get("xVersion")
        agents_str = extract_agents_for_row(root, t)
        desc = t.get("display_description") or t.get("displayDescription") or ""

        rows.append(
f"""    <tr>
      <td>{_safe(name)}</td>
      <td>{_safe(xver if xver is not None else "")}</td>
      <td>{_safe(agents_str)}</td>
      <td>{_safe(desc)}</td>
      <td>{storybook_link(name) if name else ""}</td>
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
    url = f"{conf_url}/rest/api/content/{page_id}?expand=version"
    headers = {"Accept": "application/json", "Authorization": auth}
    return _http("GET", url, headers)

def confluence_put_storage(conf_url: str, auth: str, page_id: int, title: str, html_body: str, next_version: int, message: str = "") -> Dict[str, Any]:
    """
    ВАЖНО: ancestors НЕ ПЕРЕДАЁМ, чтобы НЕ ломать иерархию.
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

# -------------------- MAIN --------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="build-meta → widget-meta.json → таблица → Confluence")
    ap.add_argument("--script", required=True, help="Путь к scripts/build-meta-from-zod.ts")
    ap.add_argument("--outdir", default="./output", help="Папка для JSON ОТНОСИТЕЛЬНО ТЕКУЩЕЙ ДИРЕКТОРИИ (default ./output)")
    ap.add_argument("--outfile", default="widget-meta.json", help="Имя файла JSON (default widget-meta.json)")
    ap.add_argument("--timeout", type=int, default=300, help="Таймаут TS, сек (default 300)")
    ap.add_argument("--wait", type=int, default=120, help="Таймаут ожидания JSON, сек (default 120)")
    ap.add_argument("--page-id", type=int, default=int(os.getenv("CONF_PAGE_ID", "21609790602")), help="Confluence pageId")
    args = ap.parse_args()

    # 1) пути
    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Не найден скрипт: {script}")

    # outdir — ОТНОСИТЕЛЬНО ТЕКУЩЕЙ ДИРЕКТОРИИ
    outdir = (Path.cwd() / Path(args.outdir)).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    # 2) запускаем TS (без npx), ждём JSON
    run_ts(script, timeout_sec=args.timeout)
    print("> Ожидание файла:", outfile)
    t0 = time.time()
    while time.time() - t0 < args.wait:
        if outfile.exists() and outfile.stat().st_size > 0:
            break
        time.sleep(0.2)
    if not outfile.exists():
        raise TimeoutError(f"Файл не появился за {args.wait} сек: {outfile}")
    print(f"> Найден файл: {outfile} ({outfile.stat().st_size} bytes)")

    # 3) читаем JSON
    try:
        with outfile.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать JSON: {e}")

    if not isinstance(data, dict):
        data = {"data": data}

    # 4) строим HTML-таблицу
    table_html = build_table_html(data)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    page_html = f"""
<p><strong>Widget meta</strong> — обновлено: {html.escape(ts)}</p>
{table_html}
""".strip()

    # 5) Confluence creds
    conf_url = os.getenv("CONF_URL")
    conf_user = os.getenv("CONF_USER")
    conf_pass = os.getenv("CONF_PASS")
    page_id = args.page_id

    if not conf_url or not conf_pass:
        raise RuntimeError("Задайте ENV: CONF_URL и CONF_PASS (и при Basic — CONF_USER).")

    auth = _auth_header(conf_user, conf_pass)

    # 6) читаем текущую страницу (без ancestors) и записываем новую версию
    page = confluence_get_page(conf_url, auth, page_id)
    title = page.get("title") or f"Page {page_id}"
    next_version = int(page.get("version", {}).get("number", 0)) + 1

    _ = confluence_put_storage(
        conf_url, auth, page_id,
        title, page_html, next_version,
        message="Автообновление таблицы widget-meta.json"
    )

    print(f"✅ Обновлено: pageId={page_id} (версия {next_version})")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
