#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Объединённый скрипт:

1) Запускает build-meta-from-zod.ts (если есть раннер node/bun/pnpm/npm/yarn),
   ждёт JSON (--outdir/--outfile) с метаданными виджетов.
2) Сам сканирует DEV/IFT (mf-stats.json + container.js) по диапазону версий,
   собирает опубликованные виджеты и сохраняет JSON (как find_published_widgets_hardened.py).
3) Объединяет данные: к каждой строке из widget-meta.json добавляет версии по окружениям.
4) Собирает HTML-таблицу и заливает её в Confluence.

ENV:
  CONF_URL       — https://confluence.example.com
  CONF_USER      — логин/email (опционально, если используете PAT)
  CONF_PASS      — пароль / API токен / PAT (обязательно)
  CONF_PAGE_ID   — pageId (опционально; можно передать флагом --page-id)

Пример запуска:

  python widgets_to_confluence_combined.py ^
    --script .\widget-store\scripts\build-meta-from-zod.ts ^
    --outdir .\widget-store\output ^
    --outfile widget-meta.json ^
    --versions 20-30 ^
    --page-id 21609790602

"""

import argparse
import base64
import html
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ==========================
#   Общие вспомогательные
# ==========================


def _read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _find_node_runner(name: str) -> Optional[Path]:
    """
    Ищем name или name.cmd в PATH.
    """
    env_path = os.environ.get("PATH", "")
    sep = ";" if os.name == "nt" else ":"
    for p in env_path.split(sep):
        if not p:
            continue
        bin_dir = Path(p)
        for candidate in (name, f"{name}.cmd"):
            exe = bin_dir / candidate
            if exe.exists() and exe.is_file():
                return exe.resolve()
    return None


def _maybe_run_ts(script: Path, project_root: Path, timeout: int) -> None:
    """
    Пытаемся запустить build-meta-from-zod.ts разными способами.
    Порядок:
      1) node -r ts-node/register (CJS — актуально при ошибке "exports is not defined in ES module scope")
      2) node --loader ts-node/esm (ESM)
      3) bun run
      4) pnpm exec ts-node
      5) npm exec ts-node
      6) yarn ts-node

    Если ВСЕ варианты падают, НЕ выбрасываем ошибку сразу:
    возможно, widget-meta.json уже был сгенерирован ранее.
    Тогда дальше main() сам проверит наличие файла.

    Дополнительно: игнорируем OSError (в т.ч. [WinError 193]) и пробуем следующий раннер.
    """
    runners: List[List[str]] = []

    node = _find_node_runner("node")
    if node:
        node = node.resolve()
        # СНАЧАЛА CJS
        runners.append([str(node), "-r", "ts-node/register", str(script)])
        # ПОТОМ ESM — вдруг проект станет "type": "module"
        runners.append([str(node), "--loader", "ts-node/esm", str(script)])

    bun = _find_node_runner("bun")
    if bun:
        runners.append([str(bun.resolve()), "run", str(script)])

    pnpm = _find_node_runner("pnpm")
    if pnpm:
        runners.append([str(pnpm.resolve()), "exec", "ts-node", str(script)])

    npm = _find_node_runner("npm")
    if npm:
        runners.append([str(npm.resolve()), "exec", "ts-node", str(script)])

    yarn = _find_node_runner("yarn")
    if yarn:
        runners.append([str(yarn.resolve()), "ts-node", str(script)])

    if not runners:
        print("> Не найден ни один раннер node/bun/pnpm/npm/yarn — предполагаем, что JSON уже подготовлен.", file=sys.stderr)
        return

    last_err: Optional[str] = None

    for cmd in runners:
        print("> Запуск TS скрипта:", " ".join(cmd))
        try:
            subprocess.run(cmd, cwd=str(project_root), check=True, timeout=timeout)
            print("> TS скрипт успешно завершился.")
            return
        except subprocess.TimeoutExpired:
            last_err = f"Таймаут при запуске: {' '.join(cmd)}"
            print("⚠️", last_err, file=sys.stderr)
        except subprocess.CalledProcessError as e:
            last_err = f"Код выхода {e.returncode} при запуске: {' '.join(cmd)}"
            print("⚠️", last_err, file=sys.stderr)
        except OSError as e:
            # Например [WinError 193] %1 is not a valid Win32 application
            last_err = f"OSError при запуске ({e.errno}): {e}"
            print("⚠️", last_err, file=sys.stderr)
            # пробуем следующий раннер
            continue

    print("⚠️ Все варианты запуска build-meta-from-zod.ts завершились ошибкой.", file=sys.stderr)
    if last_err:
        print("   Последняя ошибка:", last_err, file=sys.stderr)
    print("   Продолжаю выполнение, если widget-meta.json уже существует.", file=sys.stderr)


def _safe(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


# =====================================
#   Логика поиска опубликованных виджетов
#   (адаптация find_published_widgets_hardened.py)
# =====================================

DEFAULT_DEV_BASE = "https://cms-res-web.online.sberbank.ru/da-sdk-b2c/widget-store/widget-store"
DEFAULT_IFT_BASE = "https://cms-res-web.iftonline.sberbank.ru/SBERCMS/da-sdk-b2c/widget-store/widget-store"

DEFAULT_TIMEOUT = 10.0
DEFAULT_UA = "Mozilla/5.0 (compatible; WidgetScanner/1.0; +https://sberbank.ru)"


def _build_candidates(base: str, version: int, filename: str) -> List[str]:
    base = (base or "").rstrip("/")
    fn = (filename or "").lstrip("/")
    v = str(version)
    urls = [
        f"{base}/{v}/{fn}",
        f"{base}/v{v}/{fn}",
        f"{base}/release/{v}/{fn}",
    ]
    out: List[str] = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out


def _make_context(insecure: bool) -> Optional[ssl.SSLContext]:
    if not insecure:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _headers() -> dict:
    return {
        "Accept": "application/json, */*;q=0.1",
        "User-Agent": DEFAULT_UA,
    }


def _http_get_json_any(
    urls: List[str],
    timeout: float,
    ctx: Optional[ssl.SSLContext],
    verbose_prefix: str = "",
) -> Tuple[bool, Optional[str], Optional[int], Any, Optional[str]]:
    last_err: Optional[str] = None
    for u in urls:
        if verbose_prefix:
            print(f"↗️  {verbose_prefix}GET  {u}")
        req = urllib.request.Request(u, method="GET", headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                status = getattr(resp, "status", 200)
                if 200 <= status < 400:
                    data = resp.read()
                    try:
                        obj = json.loads(data.decode("utf-8", "ignore"))
                        if verbose_prefix:
                            cnt = len(obj.get("exposes") or []) if isinstance(obj, dict) else 0
                            print(f"↘️  {verbose_prefix}{status} OK JSON ({cnt} widgets)")
                        return True, u, status, obj, None
                    except Exception as e:
                        last_err = f"Invalid JSON: {e}"
                        continue
                else:
                    last_err = f"HTTP {status}"
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except Exception as e:
            last_err = str(e)
    return False, None, None, None, last_err


def _http_head_any(
    urls: List[str],
    timeout: float,
    ctx: Optional[ssl.SSLContext],
    verbose_prefix: str = "",
) -> Tuple[bool, Optional[str], Optional[int], Optional[str]]:
    last_err: Optional[str] = None
    for u in urls:
        if verbose_prefix:
            print(f"↗️  {verbose_prefix}HEAD {u}")
        req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": DEFAULT_UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                status = getattr(resp, "status", 200)
                ok = 200 <= status < 400
                if verbose_prefix:
                    print(f"↘️  {verbose_prefix}{status} HEAD")
                if ok:
                    return True, u, status, None
                else:
                    last_err = f"HTTP {status}"
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except Exception as e:
            last_err = str(e)
    return False, None, None, last_err


def normalize_widget_name(raw: Any) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    s = s.replace("_", "-").replace(" ", "-")
    while "--" in s:
        s = s.replace("--", "-")
    return s.lower()


def create_display_name(raw: Any) -> str:
    if not raw:
        return "unknown"
    s = str(raw).strip().replace("_", "-").replace(" ", "-")
    while "--" in s:
        s = s.replace("--", "-")
    return s.lower()


def ensure_widget_entry(store: Dict[str, Dict[str, Any]], key: str, display_name: str) -> Dict[str, Any]:
    if key not in store:
        store[key] = {"name": display_name or key, "environments": {}}
    entry = store[key]
    if not entry.get("name") and display_name:
        entry["name"] = display_name
    return entry


def expand_range(start: int, end: int) -> List[int]:
    lo, hi = (start, end) if start <= end else (end, start)
    return list(range(lo, hi + 1))


def parse_range_token(token: str) -> List[int]:
    token = (token or "").strip()
    import re as _re

    m = _re.match(r"^(\d+)\s*(?:-|\.\.)\s*(\d+)$", token)
    if not m:
        raise ValueError(f'Invalid range token "{token}". Ожидался формат "start-end" или "start..end"')
    a, b = int(m.group(1)), int(m.group(2))
    return expand_range(a, b)


def parse_versions_arg(text: str) -> List[int]:
    out: List[int] = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            out.append(int(part))
        elif "-" in part or ".." in part:
            out.extend(parse_range_token(part))
        else:
            raise ValueError(f'Invalid version token "{part}"')
    return out


def fetch_version(
    env_key: str,
    base_url: str,
    version: int,
    timeout: float,
    ctx: Optional[ssl.SSLContext],
    verbose: bool,
) -> Dict[str, Any]:
    prefix = f"[{env_key}] " if verbose else ""
    stats_urls = _build_candidates(base_url, version, "mf-stats.json")
    cont_urls = _build_candidates(base_url, version, "container.js")

    ok_stats, stats_url, status_stats, data_stats, err_stats = _http_get_json_any(
        stats_urls, timeout, ctx, prefix
    )
    ok_container, container_url, status_container, err_container = _http_head_any(
        cont_urls, timeout, ctx, prefix
    )

    widgets: List[str] = []
    if ok_stats and isinstance(data_stats, dict):
        exposes = data_stats.get("exposes")
        if isinstance(exposes, list):
            for e in exposes:
                if isinstance(e, dict) and e.get("name"):
                    widgets.append(str(e["name"]))

    return {
        "env": env_key,
        "version": version,
        "widgets": widgets,
        "stats_ok": ok_stats,
        "stats_status": status_stats,
        "stats_error": err_stats,
        "stats_url": stats_url,
        "container_ok": ok_container,
        "container_status": status_container,
        "container_error": err_container,
        "container_url": container_url,
    }


def collect_published_widgets(
    versions: List[int],
    dev_base: str,
    ift_base: str,
    timeout: float,
    insecure_all: bool,
    insecure_ift: bool,
    verbose: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Возвращает (payload, diagnostics), как делал find_published_widgets_hardened.py."""
    if not versions:
        return [], []

    insecure_all = bool(insecure_all)
    insecure_ift = bool(insecure_ift)

    ctx_dev = _make_context(insecure_all)
    # по умолчанию IFT — insecure, если явно не включили strict-tls
    ctx_ift = _make_context(insecure_all or insecure_ift)

    envs = [
        {"key": "DEV", "label": "Dev", "base": dev_base, "ctx": ctx_dev},
        {"key": "IFT", "label": "IFT", "base": ift_base, "ctx": ctx_ift},
    ]

    widget_map: Dict[str, Dict[str, Any]] = {}
    diagnostics: List[Dict[str, Any]] = []

    for env in envs:
        for v in versions:
            info = fetch_version(env["key"], env["base"], v, timeout, env["ctx"], verbose)

            if not info["stats_ok"]:
                diagnostics.append({
                    "environment": env["key"],
                    "version": v,
                    "target": "mf-stats.json",
                    "status": info["stats_status"],
                    "error": info["stats_error"],
                    "url": info["stats_url"],
                })
            if not info["container_ok"]:
                diagnostics.append({
                    "environment": env["key"],
                    "version": v,
                    "target": "container.js",
                    "status": info["container_status"],
                    "error": info["container_error"],
                    "url": info["container_url"],
                })

            for raw in info["widgets"]:
                normalized = normalize_widget_name(raw)
                if not normalized:
                    continue
                display = create_display_name(raw)
                entry = ensure_widget_entry(widget_map, normalized, display)
                entry["environments"].setdefault(env["key"], []).append({"releaseVersion": v})

    payload: List[Dict[str, Any]] = []
    for entry in sorted(widget_map.values(), key=lambda x: (x.get("name") or "")):
        row: Dict[str, Any] = {"widget": entry.get("name")}
        for env in envs:
            key = env["key"]
            items = entry["environments"].get(key, [])
            items = sorted(items, key=lambda it: it.get("releaseVersion", 0))
            row[key] = items
        payload.append(row)

    return payload, diagnostics


# =====================================
#       Построение HTML-таблицы
# =====================================

def _agents_list(item: Dict[str, Any]) -> str:
    agents = item.get("agents") or item.get("agent") or []
    if isinstance(agents, str):
        return agents
    if not isinstance(agents, (list, tuple)):
        return ""
    return ", ".join(str(a) for a in agents)


def _extract_display_description(item: Dict[str, Any]) -> str:
    for key in ("display_description", "displayDescription", "description"):
        if key in item and item[key]:
            return str(item[key])
    return ""


def _storybook_link(name: str) -> str:
    slug = (name or "").replace("_", "")
    url = f"http://10.53.31.7:6001/public-storybook/?path=/docs/widget-store_widgets-{slug}--docs"
    return f'<a href="{_safe(url)}">{_safe(url)}</a>'


def _normalize_widget_key(name: str) -> str:
    return normalize_widget_name(name)


def render_table_html(items: List[Dict[str, Any]], env_keys: List[str]) -> str:
    env_keys = env_keys or []
    base_cols = 5
    cols_count = base_cols + len(env_keys)
    colgroup = "<col/>" * cols_count

    head_lines: List[str] = [
        '<table class="wrapped">',
        f"  <colgroup>{colgroup}</colgroup>",
        "  <tbody>",
        "    <tr>",
        '      <th scope="col">name</th>',
        '      <th scope="col">xVersion</th>',
        '      <th scope="col">agents</th>',
        '      <th scope="col">display_description</th>',
        '      <th scope="col">Storybook</th>',
    ]
    for env in env_keys:
        head_lines.append(f'      <th scope="col">{_safe(env)} versions</th>')
    head_lines.append("    </tr>")
    head = "\n".join(head_lines)

    rows: List[str] = []
    for it in items:
        name = str(it.get("name") or "")
        xver = it.get("xVersion")
        agents = _agents_list(it)
        desc = _extract_display_description(it)
        link = _storybook_link(name) if name else ""

        row_cells: List[str] = [
            f"      <td>{_safe(name)}</td>",
            f"      <td>{_safe(xver if xver is not None else '')}</td>",
            f"      <td>{_safe(agents)}</td>",
            f"      <td>{_safe(desc)}</td>",
            f"      <td>{link}</td>",
        ]

        env_versions = it.get("env_versions") or {}
        if isinstance(env_versions, dict):
            for env in env_keys:
                vs = env_versions.get(env) or []
                if isinstance(vs, (list, tuple)):
                    vs_str = ", ".join(str(v) for v in vs)
                else:
                    vs_str = str(vs)
                row_cells.append(f"      <td>{_safe(vs_str)}</td>")

        rows.append("    <tr>\n" + "\n".join(row_cells) + "\n    </tr>")

    tail = "\n  </tbody>\n</table>"
    return "\n".join([head] + rows + [tail])


# =====================================
#       Confluence REST
# =====================================

def _auth_header(user: Optional[str], pwd: str) -> str:
    raw = f"{user or ''}:{pwd}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _http(method: str, url: str, headers: Dict[str, str], data: Optional[bytes] = None) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            status = getattr(resp, "status", 200)
            return {"status": status, "body": body}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read()}
    except urllib.error.URLError as e:
        return {"status": None, "error": str(e), "body": b""}


def confluence_get_page(conf_url: str, auth: str, page_id: int) -> Dict[str, Any]:
    url = f"{conf_url}/rest/api/content/{page_id}?expand=body.storage,version,ancestors"
    headers = {
        "Authorization": auth,
        "Accept": "application/json",
    }
    resp = _http("GET", url, headers)
    if resp.get("status") not in (200,):
        raise RuntimeError(f"Confluence GET {url} failed: {resp}")
    try:
        return json.loads(resp["body"].decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Failed to parse Confluence GET response: {e}")


def _parent_ancestor(ancestors: Any) -> Optional[List[Dict[str, Any]]]:
    try:
        if ancestors and isinstance(ancestors, (list, tuple)):
            last = ancestors[-1]
            pid = str(last.get("id") or last.get("content", {}).get("id"))
            if pid:
                return [{"id": pid}]
    except Exception:
        pass
    return None


def confluence_put_storage(
    conf_url: str,
    auth: str,
    page_id: int,
    title: str,
    storage_html: str,
    version: int,
    ancestors=None,
    message: str = "",
) -> Dict[str, Any]:
    url = f"{conf_url}/rest/api/content/{page_id}"
    payload: Dict[str, Any] = {
        "id": str(page_id),
        "type": "page",
        "title": title,
        "version": {
            "number": version,
            "message": message or "Auto update",
        },
        "body": {
            "storage": {
                "value": storage_html,
                "representation": "storage",
            }
        },
    }
    parent = _parent_ancestor(ancestors)
    if parent is not None:
        payload["ancestors"] = parent

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": auth,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resp = _http("PUT", url, headers, data=data)
    if resp.get("status") not in (200, 201):
        raise RuntimeError(f"Confluence PUT {url} failed: {resp}")
    try:
        return json.loads(resp["body"].decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Failed to parse Confluence PUT response: {e}")


# =====================================
#                 main
# =====================================

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Собрать таблицу из widget-meta.json, дополнить версиями DEV/IFT и записать в Confluence"
    )

    # Чтение widget-meta.json
    ap.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    ap.add_argument("--outdir", required=True, help="Папка вывода (относительно ТЕКУЩЕЙ рабочей директории)")
    ap.add_argument("--outfile", default="widget-meta.json", help="Имя JSON файла (default: widget-meta.json)")
    ap.add_argument("--timeout", type=int, default=300, help="Таймаут запуска TS, сек (default 300)")
    ap.add_argument("--wait", type=int, default=120, help="Таймаут ожидания JSON, сек (default 120)")

    # Диапазон версий для сканирования опубликованных виджетов
    ap.add_argument(
        "--versions",
        help='Диапазон(ы) версий: "20-30", "15..20" или список "12,14,18". Если не задано — сканирование отключено.',
    )
    ap.add_argument("--from", dest="from_v", type=int, help="Начало диапазона версий (включительно)")
    ap.add_argument("--to", dest="to_v", type=int, help="Конец диапазона версий (включительно)")

    # Параметры окружений
    ap.add_argument("--dev-base", dest="dev_base", default=os.getenv("WIDGET_STORE_DEV_BASE", DEFAULT_DEV_BASE))
    ap.add_argument("--ift-base", dest="ift_base", default=os.getenv("WIDGET_STORE_IFT_BASE", DEFAULT_IFT_BASE))
    ap.add_argument(
        "--scan-timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Таймаут HTTP при сканировании (default {DEFAULT_TIMEOUT})",
    )
    ap.add_argument("--insecure", action="store_true", help="Отключить проверку TLS-сертификата для всех хостов")
    ap.add_argument(
        "--strict-tls",
        action="store_true",
        help="Жёсткая проверка TLS и для IFT (по умолчанию IFT — insecure)",
    )
    ap.add_argument(
        "--scan-json",
        dest="scan_json",
        default=None,
        help="Куда сохранить JSON с опубликованными виджетами (по умолчанию outdir/published-widgets.json)",
    )
    ap.add_argument("--verbose-scan", action="store_true", help="Подробные логи HTTP при сканировании")

    # Confluence
    ap.add_argument(
        "--page-id",
        type=int,
        default=int(os.getenv("CONF_PAGE_ID", "21609790602")),
        help="Confluence pageId",
    )

    args = ap.parse_args()

    # --- пути ---
    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Не найден скрипт: {script}")

    outdir = (
        (Path(os.getcwd()) / args.outdir).resolve()
        if not Path(args.outdir).is_absolute()
        else Path(args.outdir).resolve()
    )
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    project_root = script.parents[1] if script.parent.name.lower() in {"scripts", "script"} else script.parent

    # --- запускаем TS, чтобы получить widget-meta.json ---
    _maybe_run_ts(script, project_root, timeout=args.timeout)

    # --- ждём JSON с метаданными ---
    print("> Ожидание файла widget-meta:", outfile)
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
            meta_data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать JSON с метаданными: {e}")

    if not isinstance(meta_data, list):
        raise RuntimeError("Ожидался массив объектов с виджетами в widget-meta.json")

    # --- собираем опубликованные виджеты по версиям (DEV/IFT) ---
    versions: List[int] = []
    if args.versions:
        versions.extend(parse_versions_arg(args.versions))
    if args.from_v is not None and args.to_v is not None:
        versions.extend(expand_range(args.from_v, args.to_v))
    versions = sorted(set(versions))

    widgets_payload: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    if versions:
        print(f"> Сканируем опубликованные виджеты для версий: {versions}")
        insecure_all = bool(args.insecure)
        insecure_ift = not args.strict_tls  # как в hardened-скрипте
        widgets_payload, diagnostics = collect_published_widgets(
            versions=versions,
            dev_base=args.dev_base,
            ift_base=args.ift_base,
            timeout=args.scan_timeout,
            insecure_all=insecure_all,
            insecure_ift=insecure_ift,
            verbose=args.verbose_scan,
        )

        # сохраняем JSON, как это делал отдельный скрипт
        scan_json_path = Path(args.scan_json) if args.scan_json else (outdir / "published-widgets.json")
        scan_json_path = scan_json_path.resolve()
        with scan_json_path.open("w", encoding="utf-8") as f:
            json.dump(widgets_payload, f, ensure_ascii=False, indent=2)
        print(f"✅ Saved JSON with {len(widgets_payload)} widgets to: {scan_json_path}")

        if diagnostics:
            print("\n⚠️  Some versions had issues:", file=sys.stderr)
            for d in diagnostics:
                status = f"HTTP {d['status']}" if d.get("status") else (d.get("error") or "Unknown error")
                tgt = f" [{d['target']}]" if d.get("target") else ""
                print(
                    f"   • {d['environment']} v{d['version']}{tgt}: {status} ({d.get('url','')})",
                    file=sys.stderr,
                )
            if not args.verbose_scan:
                print("   Use --verbose-scan to see request logs.", file=sys.stderr)
    else:
        print("> Диапазон версий не задан — столбцы DEV/IFT добавлены не будут.", file=sys.stderr)

    # --- превращаем widgets_payload в map: widget -> env -> [versions] ---
    env_keys: List[str] = []
    versions_by_widget: Dict[str, Dict[str, List[str]]] = {}
    if widgets_payload:
        env_set = set()
        for row in widgets_payload:
            raw_name = row.get("widget")
            if not raw_name:
                continue
            key = _normalize_widget_key(str(raw_name))
            env_map: Dict[str, List[str]] = {}
            for k, v in row.items():
                if k == "widget":
                    continue
                if isinstance(v, list):
                    vers: List[str] = []
                    for item in v:
                        if isinstance(item, dict) and item.get("releaseVersion") is not None:
                            vers.append(str(item["releaseVersion"]))
                    if vers:
                        env_map[k] = vers
                        env_set.add(k)
            if env_map:
                versions_by_widget[key] = env_map
        env_keys = sorted(env_set)

    # --- подмешиваем env_versions в meta_data ---
    if versions_by_widget:
        for it in meta_data:
            name = str(it.get("name") or it.get("widget") or "")
            key = _normalize_widget_key(name)
            env_map = versions_by_widget.get(key)
            if env_map:
                it["env_versions"] = env_map
    else:
        env_keys = []

    # --- строим HTML-таблицу ---
    table_html = render_table_html(meta_data, env_keys)
    page_html = f"""
<p><strong>Widget meta table</strong> — обновлено: {html.escape(time.strftime('%Y-%m-%d %H:%M:%S'))}</p>
{table_html}
""".strip()

    # --- отправляем в Confluence ---
    conf_url = os.getenv("CONF_URL")
    conf_user = os.getenv("CONF_USER") or ""
    conf_pass = os.getenv("CONF_PASS")
    page_id = args.page_id

    if not conf_url:
        raise RuntimeError("CONF_URL не задан")
    if not conf_pass:
        raise RuntimeError("CONF_PASS не задан")

    auth = _auth_header(conf_user, conf_pass)
    page = confluence_get_page(conf_url, auth, page_id)
    title = page.get("title") or "Widget meta table"
    version_obj = page.get("version") or {}
    current_version = int(version_obj.get("number") or 1)
    next_version = current_version + 1
    ancestors = page.get("ancestors")

    print(f"> Обновляем страницу pageId={page_id}, версия {current_version} -> {next_version}")
    confluence_put_storage(
        conf_url,
        auth,
        page_id,
        title,
        page_html,
        next_version,
        ancestors=_parent_ancestor(ancestors),
        message="Автообновление: таблица (name, xVersion, agents, display_description, Storybook, DEV/IFT versions)",
    )
    print(f"✅ Обновлено: pageId={page_id} (версия {next_version})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
