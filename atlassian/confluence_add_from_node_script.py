#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Однофайловый апдейтер Confluence:
1) Запускает Node-скрипт (find-release-branches.mjs) с флагом --json
2) Рендерит HTML-таблицу
3) Обновляет страницу Confluence через REST API (GET + PUT с инкрементом версии)

Переменные окружения:
  CONF_URL   — базовый URL Confluence, напр. https://confluence.company.ru
  CONF_USER  — логин или email (для Basic Auth) ИЛИ оставьте пустым при PAT
  CONF_PASS  — пароль или API token / PAT
  CONF_PAGE_ID — (опционально) целевой pageId, по умолчанию 21609790602
  NODE_SCRIPT — (опционально) путь к Node-скрипту, по умолчанию ./find-release-branches.mjs
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional, Tuple
import base64
import urllib.request
import urllib.error


# ====== Настройки ======
PAGE_ID = int(os.getenv("CONF_PAGE_ID", "21609790602"))
NODE_SCRIPT = os.getenv("NODE_SCRIPT", "./find-release-branches.mjs")

CONF_URL = os.getenv("CONF_URL")            # например: https://confluence.sberbank.ru
CONF_USER = os.getenv("CONF_USER")          # email/username (при Basic) — можно оставить пустым при PAT
CONF_PASS = os.getenv("CONF_PASS")          # пароль или API Token / Personal Access Token (PAT)

if not CONF_URL or not CONF_PASS or (CONF_USER is None and not CONF_PASS):
    print("❌ Убедитесь, что заданы: CONF_URL, CONF_PASS (и при Basic — CONF_USER).", file=sys.stderr)
    sys.exit(2)

# ====== Минимальный HTTP-клиент поверх urllib (без внешних зависимостей) ======
def _auth_header(user: Optional[str], pwd: str) -> Tuple[str, str]:
    # Confluence Data Center/Server: Basic (user:pass) или PAT как user пустой + токен в pass -> 'user:token'.
    # В обоих случаях это просто Basic base64(user:pass)
    raw = f"{user or ''}:{pwd}".encode("utf-8")
    token = base64.b64encode(raw).decode("ascii")
    return ("Authorization", f"Basic {token}")

def _request(method: str, url: str, headers: Dict[str, str], data: Optional[bytes] = None) -> Tuple[int, Dict[str, Any]]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            status = resp.getcode()
            if body:
                try:
                    return status, json.loads(body.decode("utf-8"))
                except Exception:
                    return status, {"_raw": body.decode("utf-8", "ignore")}
            return status, {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {err_body}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"HTTP error: {e}") from None

def confluence_get_page_info(page_id: int) -> Dict[str, Any]:
    url = f"{CONF_URL}/rest/api/content/{page_id}?expand=ancestors,version"
    headers = {
        "Accept": "application/json",
        _auth_header(CONF_USER, CONF_PASS)[0]: _auth_header(CONF_USER, CONF_PASS)[1],
    }
    status, body = _request("GET", url, headers)
    if status != 200:
        raise RuntimeError(f"GET page failed: status={status}")
    return body

def confluence_put_storage(page_id: int, new_title: str, new_html: str, next_version: int, ancestors: Optional[List[Dict[str, Any]]], message: str = "") -> Dict[str, Any]:
    url = f"{CONF_URL}/rest/api/content/{page_id}"
    payload = {
        "id": str(page_id),
        "type": "page",
        "title": new_title,
        "version": {
            "number": next_version,
            "minorEdit": True,
            "message": message or "",
        },
        "body": {
            "storage": {
                "representation": "storage",
                "value": new_html,
            }
        },
    }
    if ancestors:
        # В вашем SDK предок передаётся урезанным словарём; REST переживает и без ancestors.
        payload["ancestors"] = ancestors

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        _auth_header(CONF_USER, CONF_PASS)[0]: _auth_header(CONF_USER, CONF_PASS)[1],
    }
    status, body = _request("PUT", url, headers, data=json.dumps(payload).encode("utf-8"))
    if status not in (200):
        raise RuntimeError(f"PUT page failed: status={status}, body={body}")
    return body


# ====== Запуск Node и чтение JSON ======
def run_node_and_get_json(node_script_path: str) -> List[Dict[str, Any]]:
    """
    Ожидаем JSON-массив объектов с полями: name, version, commit, date, author, selectedWidgets,
    а также devLive/prodLive и подробности (если есть) — как в вашем .mjs.  # noqa
    """
    cmd = ["node", node_script_path, "--json"]
    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stdout or "")
        sys.stderr.write(e.stderr or "")
        raise RuntimeError("Node-скрипт завершился с ошибкой") from None

    try:
        data = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Не удалось распарсить JSON из stdout: {e}") from None

    if not isinstance(data, list):
        raise RuntimeError("Ожидался JSON-массив с ветками")
    return data


# ====== Рендер HTML для Confluence (storage) ======
def _badge(val: Optional[bool]) -> str:
    if val is True:
        return '<span style="color:#1a7f37;">✅</span>'
    if val is False:
        return '<span style="color:#d1242f;">❌</span>'
    return '—'

def render_html(branches: List[Dict[str, Any]]) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head = """
        <tr>
            <th>Branch</th>
            <th>Version</th>
            <th>Date</th>
            <th>Author</th>
            <th>Selected Widgets</th>
            <th>DEV</th>
            <th>PROD</th>
            <th>Commit</th>
        </tr>
    """.strip()

    rows = [head]
    for b in branches:
        name = escape(str(b.get("name", "")))
        version = escape(str(b.get("version", "")))
        date = escape(str(b.get("date", "")))
        author = escape(str(b.get("author", "")))
        widgets_list = b.get("selectedWidgets") or []
        widgets = escape(", ".join(map(str, widgets_list)) or "none")
        dev = _badge(b.get("devLive"))
        prod = _badge(b.get("prodLive"))
        commit = escape((b.get("commit") or "")[:8])

        rows.append(f"""
            <tr>
                <td><code>{name}</code></td>
                <td>{version}</td>
                <td>{date}</td>
                <td>{author}</td>
                <td>{widgets}</td>
                <td style="text-align:center">{dev}</td>
                <td style="text-align:center">{prod}</td>
                <td><code>{commit}</code></td>
            </tr>
        """.strip())

    table_html = f"""
        <table data-layout="wide">
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
    """.strip()

    html = f"""
        <p><strong>Release branches status</strong> — обновлено: {escape(ts)}</p>
        {table_html}
        <p><em>Источник:</em> автоматический запуск Node-скрипта <code>{escape(os.path.basename(NODE_SCRIPT))}</code></p>
    """.strip()

    return html


# ====== Основной поток ======
def main() -> None:
    # 1) Получаем данные из Node
    branches = run_node_and_get_json(NODE_SCRIPT)

    # 2) Рендерим HTML
    html = render_html(branches)

    # 3) Читаем страницу, вычисляем next version
    info = confluence_get_page_info(PAGE_ID)
    title = info.get("title") or ""
    ancestors = info.get("ancestors") or []
    # В SDK берётся последний ancestor и чистятся служебные поля — мы можем просто передать список как есть (или вовсе не передавать).
    version = int(info.get("version", {}).get("number", 0)) + 1

    # 4) Обновляем страницу
    _ = confluence_put_storage(
        page_id=PAGE_ID,
        new_title=title,
        new_html=html,
        next_version=version,
        ancestors=ancestors if ancestors else None,
        message="Автообновление: релизные ветки из Node-скрипта",
    )

    print(f"✅ Обновлено: pageId={PAGE_ID} (веток: {len(branches)})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
