#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Маленький скрипт для получения имени страницы Confluence
и строки вида SPACEKEY:Title для include.

ENV:
  CONF_URL   — базовый URL Confluence, например https://confluence.sberbank.ru
  CONF_USER  — логин
  CONF_PASS  — пароль / токен

Пример:
  python get_conf_title.py --page-id 21609790602
"""

import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error


def _auth_header(user: str, pwd: str) -> str:
    raw = f"{user}:{pwd}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def get_page(conf_url: str, auth: str, page_id: int) -> dict:
    # space нам нужен, чтобы собрать строку SPACEKEY:Title
    url = f"{conf_url.rstrip('/')}/rest/api/content/{page_id}?expand=space"
    headers = {
        "Accept": "application/json",
        "Authorization": auth,
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"URL error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        return json.loads(data)
    except Exception as e:
        print(f"JSON parse error: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Получить title страницы Confluence и строку для include"
    )
    ap.add_argument(
        "--page-id",
        type=int,
        required=True,
        help="Confluence pageId (целое число)",
    )
    args = ap.parse_args()

    conf_url = os.getenv("CONF_URL", "").strip()
    conf_user = os.getenv("CONF_USER", "").strip()
    conf_pass = os.getenv("CONF_PASS", "").strip()

    if not conf_url or not conf_user or not conf_pass:
        print(
            "Нужно задать ENV: CONF_URL, CONF_USER, CONF_PASS",
            file=sys.stderr,
        )
        sys.exit(1)

    auth = _auth_header(conf_user, conf_pass)
    page = get_page(conf_url, auth, args.page_id)

    title = page.get("title") or f"Page {args.page_id}"
    space_key = (page.get("space") or {}).get("key")

    print(f"pageId: {args.page_id}")
    print(f"title : {title}")
    print(f"space : {space_key or '—'}")

    # Строка для include: SPACEKEY:Title (если space известен)
    if space_key:
        include_name = f"{space_key}:{title}"
    else:
        # fallback: только title
        include_name = title

    print()
    print("Строка для include в Confluence:")
    print(include_name)


if __name__ == "__main__":
    main()
