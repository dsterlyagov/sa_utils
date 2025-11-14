#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Скрипт:
  1. Запускает build-meta-from-zod.ts (если получилось найти раннер).
  2. Ждёт JSON (--outdir/--outfile) и читает его.
  3. Дополняет данные информацией о присутствии виджета на DEV/IFT (widget_presence).
  4. Читает published-widgets.json и добавляет последние releaseVersion по DEV/IFT.
  5. Рендерит HTML-таблицу и записывает её в Confluence.

Текущая таблица в Confluence имеет колонки:
  name | PROM | IFT | agents | display_description | Storybook

ENV:
  CONF_URL      — https://confluence.example.com (обязательно)
  CONF_USER     — логин/email (можно пустым, если PAT без логина)
  CONF_PASS     — пароль / API токен / PAT (обязательно)
  CONF_PAGE_ID  — pageId по умолчанию (опционально, можно передать флагом --page-id)

Пример запуска:
  python confluence_save_table_with_presence.py ^
    --script .\widget-store\scripts\build-meta-from-zod.ts ^
    --outdir .\widget-store\output ^
    --outfile widget-meta.json ^
    --published-json published-widgets.json ^
    --page-id 21609790602
"""

import argparse
import base64
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# =====================================================
#              ДОБАВЛЕНА ЗАГРУЗКА .env
# =====================================================

def _load_dotenv(path: str = ".env") -> None:
    """
    Простейшая загрузка переменных из .env в os.environ.
    Формат строк:
      KEY=VALUE
    Пустые строки и строки, начинающиеся с '#', игнорируются.
    Уже существующие переменные окружения НЕ перезаписываются.
    """
    env_path = Path(path)
    if not env_path.exists():
        return

    try:
        with env_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        print(f"Не удалось загрузить .env: {e}", file=sys.stderr)

# =====================================================
#       widget_presence (опционально импортируемый)
# =====================================================

try:
    import widget_presence  # type: ignore
except Exception as _e_wp:
    widget_presence = None
    _WIDGET_PRESENCE_IMPORT_ERROR = _e_wp
else:
    _WIDGET_PRESENCE_IMPORT_ERROR = None

# =====================================================
#       Утилиты
# =====================================================

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

    tsconfig = _read_json(project_root / "tsconfig.json") or {}
    compiler = (tsconfig.get("compilerOptions") or {})
    module = (compiler.get("module") or "").lower()

    if module.startswith("es"):
        return "esm"
    if module in {"commonjs", "cjs"}:
        return "cjs"
    return "unknown"


def _candidate_bins(project_root: Path) -> Dict[str, List[str]]:
    bin_dir = project_root / "node_modules" / ".bin"
    on_windows = os.name == "nt"

    def variants(name: str) -> List[str]:
        result: List[str] = []
        if on_windows:
            p_cmd = (bin_dir / f"{name}.cmd").resolve()
            if p_cmd.exists():
                result.append(str(p_cmd))
        p_local = (bin_dir / name).resolve()
        if p_local.exists():
            result.append(str(p_local))
        sys_bin = shutil.which(name)
        if sys_bin:
            result.append(sys_bin)
        return result

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

    for tsx in bins["tsx"]:
        runners.append([tsx, str(script)])
    if bins["npx"]:
        runners.append([bins["npx"][0], "-y", "tsx", str(script)])

    if mode in ("esm", "unknown"):
        for tsn in bins["tsnode"]:
            runners.append([tsn, "--esm", "--transpile-only", str(script)])
        runners.append([bins["node"][0], "--loader", "ts-node/esm", str(script)])

    if mode in ("cjs", "unknown"):
        for tsn in bins["tsnode"]:
            runners.append([tsn, "--transpile-only", str(script)])
        runners.append([bins["node"][0], "-r", "ts-node/register", str(script)])

    uniq: List[List[str]] = []
    seen = set()
    for cmd in runners:
        key = " ".join(cmd)
        if key not in seen:
            seen.add(key)
            uniq.append(cmd)
    return uniq


def _run_ts_once(cmd: List[str], cwd: Path, timeout_sec: int) -> None:
    print("> Запуск:", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        out, _ = proc.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise TimeoutError(f"Превышен таймаут {timeout_sec} сек")
    if out:
        sys.stdout.write(out)
    if proc.returncode != 0:
        raise RuntimeError(f"Процесс завершился с кодом {proc.returncode}")


def _maybe_run_ts(script: Path, project_root: Path, timeout: int) -> None:
    for cmd in _pick_runners(script, project_root):
        try:
            _run_ts_once(cmd, cwd=project_root, timeout_sec=timeout)
            return
        except Exception as e:
            print(f"! Runner failed: {' '.join(cmd)}")
            print(f"  Reason: {e}")
    print("! Не удалось запустить TS-скрипт — используем существующий JSON.")


def _safe(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _normalize_widget_key(name: str) -> str:
    s = name.strip().lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def _extract_display_description(item: Dict[str, Any]) -> str:
    xmeta = item.get("xMeta")
    if isinstance(xmeta, dict) and isinstance(xmeta.get("display_description"), str):
        return xmeta["display_description"]

    xpayload = item.get("xPayload")
    if isinstance(xpayload, str):
        try:
            obj = json.loads(xpayload)
            if isinstance(obj, dict) and isinstance(obj.get("display_description"), str):
                return obj["display_description"]
        except Exception:
            pass

    return ""


def _agents_list(item: Dict[str, Any]) -> str:
    agents = item.get("agents")
    if not isinstance(agents, list):
        return ""
    names: List[str] = []
    for agent in agents:
        if isinstance(agent, dict) and agent.get("name"):
            names.append(str(agent["name"]))
    return ", ".join(names)


def _storybook_link(name: str) -> str:
    slug = (name or "").replace("_", "")
    url = f"http://10.53.31.7:6001/public-storybook/?path=/docs/widget-store_widgets-{slug}--docs"
    return f'<a href="{_safe(url)}">{_safe(url)}</a>'


def _presence_for_item(item: Dict[str, Any], timeout: float = 8.0) -> Dict[str, Any]:
    name = str(item.get("name") or "").strip()
    version = item.get("xVersion")
    try:
        version_int = int(version) if version is not None else None
    except Exception:
        version_int = None

    out = dict(item)
    out.setdefault("dev_present", None)
    out.setdefault("ift_present", None)
    out.setdefault("dev_url", None)
    out.setdefault("ift_url", None)

    if not name or version_int is None:
        return out

    if widget_presence is None or not hasattr(widget_presence, "check_widget"):
        out["presence_error"] = (
            f"widget_presence import failed: {_WIDGET_PRESENCE_IMPORT_ERROR!s}"
            if _WIDGET_PRESENCE_IMPORT_ERROR
            else "widget_presence unavailable"
        )
        return out

    try:
        res = widget_presence.check_widget(name, version_int, timeout=timeout)  # type: ignore
        out["dev_present"] = bool(res.get("dev_present"))
        out["ift_present"] = bool(res.get("ift_present"))
        out["dev_url"] = res.get("dev_url")
        out["ift_url"] = res.get("ift_url")
        if res.get("normalized_name"):
            out["name"] = res["normalized_name"]
    except Exception as e:
        out["presence_error"] = str(e)

    return out

# =====================================================
#   published-widgets.json → последние версии DEV/IFT
# =====================================================

def _load_published_versions(path: Optional[Path]) -> Dict[str, Dict[str, Optional[int]]]:
    if not path or not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}

    def last_ver(items: Any) -> Optional[int]:
        versions = []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and "releaseVersion" in it:
                    try:
                        versions.append(int(it["releaseVersion"]))
                    except Exception:
                        pass
        return max(versions) if versions else None

    out: Dict[str, Dict[str, Optional[int]]] = {}

    for obj in data:
        if not isinstance(obj, dict):
            continue
        widget = str(obj.get("widget") or "").strip()
        if not widget:
            continue

        key = _normalize_widget_key(widget)
        out[key] = {
            "DEV": last_ver(obj.get("DEV") or []),
            "IFT": last_ver(obj.get("IFT") or []),
        }

    return out

# =====================================================
#                Рендер HTML таблицы
# =====================================================

def render_table_html(items: List[Dict[str, Any]], published_versions: Dict[str, Dict[str, Optional[int]]]) -> str:
    head = """
<table class="wrapped">
  <colgroup><col/><col/><col/><col/><col/><col/></colgroup>
  <tbody>
    <tr>
      <th scope="col">name</th>
      <th scope="col">PROM</th>
      <th scope="col">IFT</th>
      <th scope="col">agents</th>
      <th scope="col">display_description</th>
      <th scope="col">Storybook</th>
    </tr>
""".rstrip()

    rows = []

    for it in items:
        name = str(it.get("name") or "")
        agents = _agents_list(it)
        desc = _extract_display_description(it)
        link = _storybook_link(name) if name else ""

        key = _normalize_widget_key(name)
        pub = published_versions.get(key, {})

        prom_rel = pub.get("DEV")
        ift_rel = pub.get("IFT")

        prom_str = str(prom_rel) if prom_rel is not None else "❌"
        ift_str = str(ift_rel) if ift_rel is not None else "❌"

        rows.append(
            f"    <tr>\n"
            f"      <td>{_safe(name)}</td>\n"
            f"      <td>{_safe(prom_str)}</td>\n"
            f"      <td>{_safe(ift_str)}</td>\n"
            f"      <td>{_safe(agents)}</td>\n"
            f"      <td>{_safe(desc)}</td>\n"
            f"      <td>{link}</td>\n"
            f"    </tr>"
        )

    tail = """
  </tbody>
</table>
""".lstrip()

    return "\n".join([head] + rows + [tail])

# =====================================================
#                    Confluence REST
# =====================================================

def _auth_header(user: Optional[str], pwd: str) -> str:
    raw = f"{user or ''}:{pwd}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _http(method: str, url: str, headers: Dict[str, str], data: Optional[bytes] = None) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {e.read().decode('utf-8', 'ignore')}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"HTTP error: {e}")

    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {"_raw": body.decode("utf-8", "ignore")}


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
        "body": {"storage": {"representation": "storage", "value": html_body}},
    }
    if ancestors:
        payload["ancestors"] = ancestors

    data = json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Authorization": auth}
    return _http("PUT", url, headers, data=data)

# =====================================================
#                        Main
# =====================================================

def _wait_for_file(path: Path, wait_sec: int) -> None:
    print("> Ожидание файла:", path)
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            print(f"> Найден файл: {path} ({path.stat().st_size} bytes)")
            return
        time.sleep(0.2)
    raise RuntimeError(f"Файл не появился за {wait_sec} сек: {path}")


def _load_widget_meta(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise RuntimeError("Ожидался массив объектов с виджетами.")
    return data


def _confluence_config(args: argparse.Namespace) -> Tuple[str, Optional[str], str, int]:
    conf_url = os.getenv("CONF_URL", "").strip()
    conf_user = os.getenv("CONF_USER")
    conf_pass = os.getenv("CONF_PASS", "").strip()
    page_id = args.page_id

    if not conf_url or not conf_pass:
        raise RuntimeError("Нужно задать ENV: CONF_URL и CONF_PASS (и при Basic — CONF_USER).")

    return conf_url, conf_user, conf_pass, page_id


def main() -> None:

    # >>> Загрузка .env <<<
    _load_dotenv()

    parser = argparse.ArgumentParser(description="Собрать таблицу из widget-meta.json и записать её в Confluence")

    parser.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    parser.add_argument("--outdir", required=True, help="Папка вывода")
    parser.add_argument("--outfile", default="widget-meta.json", help="Имя JSON файла")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--wait", type=int, default=120)
    parser.add_argument("--page-id", type=int, default=int(os.getenv("CONF_PAGE_ID", "21609790602")))
    parser.add_argument("--published-json", help="JSON с релизами виджетов", default=None)

    args = parser.parse_args()

    # Пути
    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Не найден скрипт: {script}")

    outdir = (Path(os.getcwd()) / args.outdir).resolve() if not Path(args.outdir).is_absolute() else Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    project_root = script.parents[1] if script.parent.name.lower() in {"scripts", "script"} else script.parent

    _maybe_run_ts(script, project_root, timeout=args.timeout)

    _wait_for_file(outfile, args.wait)
    widgets = _load_widget_meta(outfile)

    published_path = Path(args.published_json).resolve() if args.published_json else None
    published_versions = _load_published_versions(published_path)

    enriched = [_presence_for_item(item) for item in widgets]
    table_html = render_table_html(enriched, published_versions)

    page_html = (
        f"<p><strong>Widget meta table</strong> — "
        f"обновлено: {html.escape(time.strftime('%Y-%m-%d %H:%M:%S'))}</p>\n"
        f"{table_html}"
    )

    conf_url, conf_user, conf_pass, page_id = _confluence_config(args)
    auth = _auth_header(conf_user, conf_pass)

    page = confluence_get_page(conf_url, auth, page_id)
    title = page.get("title") or f"Page {page_id}"

    ancestors = None
    anc_list = page.get("ancestors") or []
    if anc_list:
        parent_id = anc_list[-1].get("id")
        if parent_id:
            ancestors = [{"id": parent_id}]

    next_version = int(page.get("version", {}).get("number", 0)) + 1

    confluence_put_storage(
        conf_url,
        auth,
        page_id,
        title,
        page_html,
        next_version,
        ancestors=ancestors,
        message="Автообновление: таблица (name, PROM, IFT, agents, display_description, Storybook)",
    )

    print(f"✅ Обновлено: pageId={page_id} (версия {next_version})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
