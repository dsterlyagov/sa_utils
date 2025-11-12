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



# -------------------- DEV/IFT presence logic (inlined, no imports) --------------------
import ssl
import urllib.request
import urllib.error


# ---- compatibility stub (legacy TS runner hook) ----
def _maybe_run_ts(script_path, project_root, timeout=60):
    """No-op: STUB for legacy TS runner; kept for compatibility."""
    return None


DEV_BASE_DEFAULT = "https://cms-res-web.online.sberbank.ru/da-sdk-b2c/widget-store/widget-store"
IFT_BASE_DEFAULT = "https://cms-res-web.iftonline.sberbank.ru/SBERCMS/da-sdk-b2c/widget-store/widget-store"
DEFAULT_TIMEOUT = 10.0

_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE

def _norm_kebab(s: str) -> str:
    s = (s or "").strip().lower().replace("_", "-").replace(" ", "-")
    while "--" in s:
        s = s.replace("--", "-")
    return s

def _slug_variants(name: str):
    n = (name or "").strip()
    low = n.lower()
    kebab = _norm_kebab(n)
    no_underscore = low.replace("_", "")
    uniq = []
    for s in [n, low, kebab, no_underscore]:
        if s and s not in uniq:
            uniq.append(s)
    return uniq

def _build_candidates(base: str, widget: str, version: int, filename: str):
    base = base.rstrip("/")
    fn = filename.lstrip("/")
    v = str(version)
    variants = _slug_variants(widget)
    urls = []

    # 1) Base without widget segment (matches your example)
    urls.append(f"{base}/{v}/{fn}")

    # 2) Common shapes with widget segment
    for w in variants:
        urls.append(f"{base}/{w}/{v}/{fn}")          # .../widget/version/file
        urls.append(f"{base}/{v}/{w}/{fn}")          # .../version/widget/file
        urls.append(f"{base}/{w}/v{v}/{fn}")         # .../widget/vX/file

    # de-dup
    out = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out

def _http_get(url: str, timeout: float = DEFAULT_TIMEOUT):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
        return getattr(resp, "status", 200), resp.read()

def _fetch_json(url: str, timeout: float = DEFAULT_TIMEOUT):
    try:
        status, body = _http_get(url, timeout)
        return {"ok": 200 <= status < 400, "status": status, "json": json.loads(body.decode("utf-8", "ignore"))}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": getattr(e, "code", None), "error": f"HTTP {getattr(e, 'code', '')}"}
    except Exception as e:
        return {"ok": False, "status": None, "error": str(e)}

def _head_ok(url: str, timeout: float = DEFAULT_TIMEOUT):
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
            return 200 <= getattr(resp, "status", 200) < 400, getattr(resp, "status", 200), None
    except urllib.error.HTTPError as e:
        return False, getattr(e, "code", None), f"HTTP {getattr(e, 'code', '')}"
    except Exception as e:
        return False, None, str(e)

def _exposes_has_widget(stats_json: dict, widget: str) -> bool:
    """Heuristically check if mf-stats.json exposes the widget. We normalize keys like './foo' to 'foo' kebab-case."""
    if not isinstance(stats_json, dict):
        return False
    exposes = stats_json.get("exposes") or stats_json.get("exposed") or {}
    if not isinstance(exposes, dict):
        return False
    target = _norm_kebab(widget).strip("/.")
    for k in list(exposes.keys()):
        key = str(k).lstrip("./")
        # Normalize typical PascalCase too (split before capitals)
        keb = _norm_kebab(re.sub(r"(?<!^)([A-Z])", r"-\1", key))
        if keb == target:
            return True
        # also allow './widget-store/<name>' style
        if keb.endswith("/" + target):
            return True
    return False

def _check_widget_in_environment(widget: str, version: int, base_url: str, environment: str, timeout: float = DEFAULT_TIMEOUT):
    stats_present = False
    container_present = False
    chosen_stats = None
    chosen_container = None
    last_error = None
    listed_in_exposes = False

    # mf-stats.json: try shapes and also verify 'exposes' contains the widget
    for stats_url in _build_candidates(base_url, widget, version, "mf-stats.json"):
        res = _fetch_json(stats_url, timeout)
        if res.get("ok"):
            chosen_stats = stats_url
            stats_present = True
            try:
                listed_in_exposes = _exposes_has_widget(res.get("json", {}), widget)
            except Exception:
                listed_in_exposes = False
            break
        last_error = res.get("error") or last_error

    # container.js HEAD
    for container_url in _build_candidates(base_url, widget, version, "container.js"):
        ok2, _, err2 = _head_ok(container_url, timeout)
        if ok2:
            container_present = True
            chosen_container = container_url
            break
        last_error = err2 or last_error

    present = stats_present and container_present and listed_in_exposes
    return {
        "environment": environment,
        "widget": widget,
        "version": version,
        "present": present,
        "stats_url": chosen_stats,
        "container_url": chosen_container,
        "error": last_error,
        "stats_ok": stats_present,
        "head_ok": container_present,
        "listed_in_exposes": listed_in_exposes,
    }

def check_widget(widget: str, version: int, dev_base: str = DEV_BASE_DEFAULT, ift_base: str = IFT_BASE_DEFAULT, timeout: float = DEFAULT_TIMEOUT):
    version = int(version)
    dev = _check_widget_in_environment(widget, version, dev_base, "DEV", timeout)
    ift = _check_widget_in_environment(widget, version, ift_base, "IFT", timeout)
    return {
        "normalized_name": _norm_kebab(widget),
        "version": version,
        "dev_present": dev["present"],
        "ift_present": ift["present"],
        "dev_url": dev["container_url"],
        "ift_url": ift["container_url"],
        "dev_stats_url": dev["stats_url"],
        "ift_stats_url": ift["stats_url"],
        "error": dev.get("error") or ift.get("error"),
        "dev_listed": dev.get("listed_in_exposes"),
        "ift_listed": ift.get("listed_in_exposes"),
    }

def _presence_for_item(it: Dict[str, Any], timeout: float = 8.0) -> Dict[str, Any]:
    """Дополнить элемент полями доступности на DEV/IFT (dev_present, ift_present, dev_url, ift_url)."""
    name = str(it.get("name") or "").strip()
    version = it.get("xVersion")
    try:
        version_int = int(version) if version is not None else None
    except Exception:
        version_int = None
    out = dict(it)
    out.setdefault("dev_present", None)
    out.setdefault("ift_present", None)
    out.setdefault("dev_url", None)
    out.setdefault("ift_url", None)
    if not name or version_int is None:
        return out
    if False:  # widget_presence is inlined; this branch will never execute
        # импорт не удался — оставляем пустые поля
        out["presence_error"] = f"widget_presence import failed: {_WIDGET_PRESENCE_IMPORT_ERROR!s}" if _WIDGET_PRESENCE_IMPORT_ERROR else "widget_presence unavailable"
        return out
    try:
        res = check_widget(name, version_int, timeout=timeout)
        out["dev_present"] = bool(res.get("dev_present")) if res.get("dev_present") is not None else None
        out["ift_present"] = bool(res.get("ift_present")) if res.get("ift_present") is not None else None
        out["dev_url"] = res.get("dev_url")
        out["ift_url"] = res.get("ift_url")
        # нормализованное имя оставим как name2, если вернулось
        if res.get("normalized_name"):
            out["name"] = res.get("normalized_name")
    except Exception as e:
        out["presence_error"] = str(e)
    return out


def render_table_html(items: List[Dict[str, Any]]) -> str:
    head = """
<table class="wrapped">
  <colgroup><col/><col/><col/><col/><col/><col/><col/></colgroup>
  <tbody>
    <tr>
      <th scope="col">name</th>
      <th scope="col">xVersion</th>
      <th scope="col">DEV</th>
      <th scope="col">IFT</th>
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
        dev_present = it.get("dev_present")
        ift_present = it.get("ift_present")

        rows.append(
f"""    <tr>\n      <td>{_safe(name)}</td>\n      <td>{_safe(xver if xver is not None else "")}</td>\n      <td>{("✅" if dev_present is True else ("❌" if dev_present is False else "—"))}</td>\n      <td>{("✅" if ift_present is True else ("❌" if ift_present is False else "—"))}</td>\n      <td>{_safe(agents)}</td>\n      <td>{_safe(desc)}</td>\n      <td>{link}</td>\n    </tr>"""
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

    # --- дополняем данными о доступности (DEV/IFT) и строим таблицу ---
    enriched = [_presence_for_item(it) for it in data]
    table_html = render_table_html(enriched)
    page_html = f"""
<p><strong>Widget meta table</strong> — обновлено: {html.escape(time.strftime('%Y-%m-%d %H:%M:%S'))}</p>
{table_html}
""".strip()

    # --- отправляем в Confluence ---
    conf_url = ''
    conf_user = ''
    conf_pass = ''
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
        ancestors=ancestors,
        message="Автообновление: таблица (name, xVersion, agents, display_description, Storybook)"
    )
    print(f"✅ Обновлено: pageId={page_id} (версия {next_version})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
