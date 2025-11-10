#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Запускает build-meta-from-zod.ts, читает output/widget-meta.json,
строит таблицу (как в table.xml) и записывает на Confluence.

Ожидаемый JSON (корень — массив):
[
  {
    "agent": { "name": "<agentName>" },
    "toolsMeta": [
       { "name": "<widget>", "xVersion": <int>, ... },
       ...
    ]
  },
  ...
]

Таблица (колонки по шаблону):
 - "Виджет (name)"        <- toolsMeta[i].name
 - "Номер дистрибутива"   <- toolsMeta[i].xVersion
 - "Ссылка на Storybook"  <- http://10.53.31.7:6001/public-storybook/?path=/docs/widget-store_widgets-{name_without_underscores}--docs

ENV для Confluence:
  CONF_URL   — https://confluence.company.ru (обязательно)
  CONF_PASS  — пароль / API token / PAT (обязательно)
  CONF_USER  — логин/email (опционально; для Basic)
  CONF_PAGE_ID — целевая страница (опционально; по умолчанию 21609790602)

Пример запуска (Windows):
  python build_meta_to_confluence_table.py ^
    --script "C:\\...\\widget-store\\scripts\\build-meta-from-zod.ts" ^
    --outdir "C:\\...\\widget-store\\output" ^
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
from typing import Any, Dict, List, Optional, Tuple, Set

# -------------------- запуск TS --------------------

def _which(name: str) -> Optional[str]:
    return shutil.which(name)

def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def _try_read_json(path: Path) -> Optional[dict]:
    try:
        return _read_json(path)
    except Exception:
        return None

def _detect_module_mode(project_root: Path) -> str:
    """ 'esm' | 'cjs' | 'unknown' — по package.json/tsconfig.json """
    pkg = _try_read_json(project_root / "package.json") or {}
    if pkg.get("type") == "module":
        return "esm"
    tsconfig = _try_read_json(project_root / "tsconfig.json") or {}
    comp = (tsconfig.get("compilerOptions") or {})
    module = (comp.get("module") or "").lower()
    if module.startswith("es"):
        return "esm"
    if module in {"commonjs", "cjs"}:
        return "cjs"
    return "unknown"

def _candidate_bins(project_root: Path) -> Dict[str, List[str]]:
    """Пути к бинарям (локальные node_modules/.bin и системные)."""
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
    """Подбираем последовательность команд для запуска TS под ESM/CJS."""
    bins = _candidate_bins(project_root)
    mode = _detect_module_mode(project_root)

    runners: List[List[str]] = []

    # 1) TSX (локальный → npx → системный)
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

    # remove dups
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

# -------------------- сбор данных и рендер таблицы --------------------

def _safe(v: Any) -> str:
    return html.escape("" if v is None else str(v))

def _storybook_url(name: str) -> str:
    slug = (name or "").replace("_", "")
    return f"http://10.53.31.7:6001/public-storybook/?path=/docs/widget-store_widgets-{slug}--docs"

def _render_confluence_table(rows: List[Tuple[str, str, str]]) -> str:
    """
    rows: список кортежей (widget_name, version, storybook_url)
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
    </tr>
""".rstrip()

    body_parts: List[str] = []
    for name, version, url in rows:
        body_parts.append(
f"""    <tr>
      <td>{_safe(name)}</td>
      <td>{_safe(version)}</td>
      <td><a class="" href="{_safe(url)}">{_safe(url)}</a></td>
    </tr>"""
        )

    tail = """
  </tbody>
</table>
""".lstrip()

    return "\n".join([head] + body_parts + [tail])

def _aggregate_from_json(root_json: Any) -> Tuple[List[Tuple[str, str, str]], List[str]]:
    """
    Принимаем JSON из файла (корень — массив).
    Возвращаем:
      - rows: [(widget_name, version_str, storybook_url), ...]
      - agents: [unique agent names]
    """
    rows: List[Tuple[str, str, str]] = []
    agents: List[str] = []
    seen_agents: Set[str] = set()

    if not isinstance(root_json, list):
        raise ValueError("Ожидался JSON-массив на верхнем уровне.")

    for item in root_json:
        if not isinstance(item, dict):
            continue
        agent = item.get("agent") or {}
        agent_name = ""
        if isinstance(agent, dict):
            agent_name = str(agent.get("name") or "")
            if agent_name and agent_name not in seen_agents:
                seen_agents.add(agent_name)
                agents.append(agent_name)

        tools = item.get("toolsMeta")
        if not isinstance(tools, list):
            continue

        for t in tools:
            if not isinstance(t, dict):
                continue
            name = t.get("name")
            xver = t.get("xVersion")
            if not name or xver is None:
                continue
            name = str(name)
            version_str = str(xver)
            url = _storybook_url(name)
            rows.append((name, version_str, url))

    # опционально: убрать дубликаты строк (по имени виджета) и оставить максимальный xVersion
    latest: Dict[str, Tuple[str, str, str]] = {}
    for (name, ver, url) in rows:
        try:
            vnum = int(ver)
        except Exception:
            vnum = -1
        if name not in latest:
            latest[name] = (name, ver, url)
        else:
            try:
                prev = int(latest[name][1])
            except Exception:
                prev = -1
            if vnum > prev:
                latest[name] = (name, ver, url)
    dedup_rows = list(latest.values())

    # сортировка по имени
    dedup_rows.sort(key=lambda r: r[0].lower())
    return dedup_rows, agents

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
    p = argparse.ArgumentParser(description="Собрать таблицу из widget-meta.json и записать в Confluence (все агенты)")
    p.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    p.add_argument("--outdir", required=True, help="Папка output (куда пишет TS)")
    p.add_argument("--outfile", default="widget-meta.json", help="Имя JSON файла (по умолчанию widget-meta.json)")
    p.add_argument("--timeout", type=int, default=300, help="Таймаут выполнения TS, сек")
    p.add_argument("--wait", type=int, default=120, help="Таймаут ожидания JSON, сек")
    p.add_argument("--page-id", type=int, default=int(os.getenv("CONF_PAGE_ID", "21609790602")), help="Confluence pageId")
    args = p.parse_args()

    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Не найден скрипт: {script}")

    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    # Определяем корень проекта (важно для относительных импортов TS)
    project_root = script.parents[1] if script.parent.name.lower() in {"scripts", "script"} else script.parent
    cwd = project_root

    # 1) Запускаем TS
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

    # 2) Ждём JSON
    print("> Ожидание файла:", outfile)
    _wait_for_file(outfile, timeout_sec=args.wait)
    print(f"> Найден файл: {outfile} ({outfile.stat().st_size} bytes)")

    # 3) Читаем JSON и агрегируем
    try:
        root = _read_json(outfile)
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать JSON: {e}")

    rows, agents = _aggregate_from_json(root)

    if not rows:
        raise RuntimeError("Не удалось собрать строки таблицы из JSON (нет pairs name/xVersion).")

    # 4) Строим Confluence HTML (таблица + список агентов сверху)
    agents_html = ""
    if agents:
        agents_html = "<p><strong>Агенты:</strong> " + ", ".join(html.escape(a) for a in agents) + "</p>"

    table_html = _render_confluence_table(rows)
    page_html = (agents_html + "\n" + table_html).strip()

    # 5) Обновляем Confluence
    conf_url = os.getenv("CONF_URL")
    conf_user = os.getenv("CONF_USER")
    conf_pass = os.getenv("CONF_PASS")
    page_id = args.page_id

    if not conf_url or not conf_pass:
        raise RuntimeError("Нужно задать переменные окружения CONF_URL и CONF_PASS (и при Basic — CONF_USER).")

    auth = _auth_header(conf_user, conf_pass)
    page = confluence_get_page(conf_url, auth, page_id)
    title = page.get("title") or f"Page {page_id}"
    ancestors = page.get("ancestors") or []
    next_version = int(page.get("version", {}).get("number", 0)) + 1

    _ = confluence_put_storage(
        conf_url, auth, page_id,
        title, page_html, next_version,
        ancestors=ancestors,
        message="Автообновление таблицы из widget-meta.json (все агенты)"
    )

    print(f"✅ Обновлено: pageId={page_id} (версия {next_version}), строк: {len(rows)}, агенты: {len(agents)}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
