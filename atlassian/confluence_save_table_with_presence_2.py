#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
УПРОЩЁННАЯ И КОРРЕКТНАЯ ВЕРСИЯ для Windows Task Scheduler.

ГЛАВНОЕ:
 - .env загружается из директории, где лежит сам скрипт.
 - Логи печатаются в stdout (перехватываются в bat → log-файл).
 - Все пути считаются относительно BASE_DIR из .env.
 - Ошибки выводятся явно.
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


# ============================================================
#   НАДЁЖНАЯ ЗАГРУЗКА .ENV ИМЕННО ИЗ ПАПКИ СКРИПТА
# ============================================================

def load_dotenv_from_script_dir() -> None:
    script_dir = Path(__file__).resolve().parent
    env_file = script_dir / ".env"
    if not env_file.exists():
        print(f"[WARN] .env not found: {env_file}")
        return

    try:
        with env_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        print(f"[INFO] Loaded .env from {env_file}")
    except Exception as e:
        print(f"[ERROR] Failed to load .env: {e}")


# ============================================================
#   JSON READ
# ============================================================

def read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================
#   UTILS
# ============================================================

def safe(v):
    return html.escape("" if v is None else str(v))


def normalize_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.strip().lower())


# ============================================================
#   HTML TABLE
# ============================================================

def render_html(items, published_versions):
    head = """
<table class="wrapped">
  <tbody>
    <tr>
      <th>name</th>
      <th>PROM</th>
      <th>IFT</th>
      <th>agents</th>
      <th>description</th>
      <th>Storybook</th>
    </tr>
""".strip()

    rows = []
    for it in items:
        name = it.get("name", "")
        key = normalize_key(name)
        pub = published_versions.get(key, {})

        display_name = pub.get("widget", name)
        prom = pub.get("DEV")
        ift = pub.get("IFT")

        agents = ", ".join(a.get("name", "") for a in (it.get("agents") or []) if isinstance(a, dict))

        rows.append(
            f"<tr>"
            f"<td>{safe(display_name)}</td>"
            f"<td>{safe(prom or '❌')}</td>"
            f"<td>{safe(ift or '❌')}</td>"
            f"<td>{safe(agents)}</td>"
            f"<td>{safe(it.get('display_description') or '')}</td>"
            f"<td></td>"
            f"</tr>"
        )

    tail = "</tbody></table>"
    return head + "\n" + "\n".join(rows) + "\n" + tail


# ============================================================
#   CONFLUENCE REST
# ============================================================

def auth_header(user, pwd):
    raw = f"{user or ''}:{pwd}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def http(method, url, headers, data=None):
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"HTTP error: {e}")

    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except:
        return {"_raw": body.decode("utf-8", "ignore")}


def get_page(conf_url, auth, page_id):
    url = f"{conf_url}/rest/api/content/{page_id}?expand=version,ancestors"
    return http("GET", url, {"Accept": "application/json", "Authorization": auth})


def put_storage(conf_url, auth, page_id, title, html_body, next_ver, ancestors=None):
    url = f"{conf_url}/rest/api/content/{page_id}"
    payload = {
        "id": str(page_id),
        "type": "page",
        "title": title,
        "version": {"number": next_ver, "minorEdit": True},
        "body": {"storage": {"representation": "storage", "value": html_body}},
    }
    if ancestors:
        payload["ancestors"] = ancestors

    return http(
        "PUT",
        url,
        {"Accept": "application/json", "Content-Type": "application/json", "Authorization": auth},
        data=json.dumps(payload).encode("utf-8"),
    )


# ============================================================
#   MAIN
# ============================================================

def main():
    print("[INFO] Script started")

    load_dotenv_from_script_dir()

    parser = argparse.ArgumentParser()
    parser.add_argument("page_id", type=int, nargs="?")
    args = parser.parse_args()

    # ---- Confluence config ----
    conf_url = os.getenv("CONF_URL", "").strip()
    conf_user = os.getenv("CONF_USER", "")
    conf_pass = os.getenv("CONF_PASS", "").strip()
    conf_page = args.page_id or os.getenv("CONF_PAGE_ID", "").strip()

    if not conf_url or not conf_pass or not conf_page:
        raise RuntimeError("CONF_URL, CONF_PASS, CONF_PAGE_ID must be set in .env")

    try:
        page_id = int(conf_page)
    except:
        raise RuntimeError(f"Invalid CONF_PAGE_ID: {conf_page}")

    # ---- Directories ----
    script_dir = Path(__file__).resolve().parent
    base_dir = Path(os.getenv("BASE_DIR", script_dir)).resolve()

    meta_json = (base_dir / os.getenv("META_OUTDIR") / os.getenv("META_OUTFILE")).resolve()

    print(f"[INFO] Using meta JSON: {meta_json}")

    # ---- Wait meta JSON ----
    timeout = 180
    start = time.time()
    while True:
        if meta_json.exists() and meta_json.stat().st_size > 0:
            break
        if time.time() - start > timeout:
            raise RuntimeError(f"Meta JSON not found within {timeout}s: {meta_json}")
        time.sleep(0.2)

    # ---- Read JSON ----
    widgets = read_json(meta_json)
    if not isinstance(widgets, list):
        raise RuntimeError("Meta JSON must be list")

    # ---- Published widgets ----
    published_file = base_dir / os.getenv("PUBLISHED_JSON", "")
    published_versions = {}

    if published_file.exists():
        try:
            data = read_json(published_file) or []
            for obj in data:
                name = obj.get("widget")
                if not name:
                    continue
                key = normalize_key(name)
                dev = obj.get("DEV", [])
                ift = obj.get("IFT", [])
                published_versions[key] = {
                    "widget": name,
                    "DEV": max((x.get("releaseVersion") for x in dev if isinstance(x, dict) and x.get("releaseVersion")), default=None),
                    "IFT": max((x.get("releaseVersion") for x in ift if isinstance(x, dict) and x.get("releaseVersion")), default=None),
                }
        except Exception as e:
            print("[WARN] Cannot parse published-widgets:", e)

    # ---- Merge ----
    items = []
    for w in widgets:
        if "name" in w:
            items.append(w)

    for key, pub in published_versions.items():
        if not any(normalize_key(i["name"]) == key for i in items):
            items.append({"name": pub["widget"]})

    # ---- Render ----
    html_table = render_html(items, published_versions)
    page_html = f"<p><strong>Widget meta table</strong> — обновлено {time.strftime('%Y-%m-%d %H:%M:%S')}</p>\n" + html_table

    # ---- Confluence update ----
    auth = auth_header(conf_user, conf_pass)
    page = get_page(conf_url, auth, page_id)
    title = page.get("title", f"Page {page_id}")
    next_ver = page.get("version", {}).get("number", 0) + 1
    anc = page.get("ancestors") or []
    ancestors = [{"id": anc[-1]["id"]}] if anc else None

    print("[INFO] Updating Confluence page...")
    put_storage(conf_url, auth, page_id, title, page_html, next_ver, ancestors)
    print(f"[OK] Updated page {page_id}, version={next_ver}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
