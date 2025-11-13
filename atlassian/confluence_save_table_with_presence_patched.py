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
    --outdir .\widget-store\dist\meta ^
    --outfile widget-meta.json ^
    --page-id 21609790602
"""

import argparse
import html
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

try:
    import widget_presence  # type: ignore
except ImportError:
    widget_presence = None


@dataclass
class ConfluenceAuth:
    base_url: str
    user: Optional[str]
    password: str


def _env_or_raise(key: str, default: Optional[str] = None) -> str:
    val = os.getenv(key, default)
    if not val:
        raise RuntimeError(f"ENV {key} не задан и default отсутствует")
    return val


def _run_build_script(script_path: Path, outdir: Path, outfile: str, timeout: int = 300) -> Path:
    """
    Запускает build-meta-from-zod.ts через npx ts-node, ожидая, что он положит JSON в outdir/outfile.
    """
    start = time.time()
    cmd = [
        "npx",
        "ts-node",
        str(script_path),
        "--outdir",
        str(outdir),
        "--outfile",
        outfile,
    ]
    print(f"> Запуск скрипта: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Стримим лог, чтобы в CI было видно прогресс
    while True:
        if proc.poll() is not None:
            break
        line = proc.stdout.readline() if proc.stdout else ""
        if line:
            print(line.rstrip())
        if time.time() - start > timeout:
            proc.kill()
            raise TimeoutError(f"build-meta-from-zod.ts не завершился за {timeout} секунд")
    # дочитываем остаток
    if proc.stdout:
        for line in proc.stdout:
            print(line.rstrip())

    if proc.returncode != 0:
        raise RuntimeError(f"Скрипт завершился с кодом {proc.returncode}")

    result_path = outdir / outfile
    if not result_path.exists():
        raise FileNotFoundError(f"Ожидался файл {result_path}, но он не создан скриптом")
    return result_path


def _wait_for_file(path: Path, timeout: int = 60) -> None:
    """
    Иногда build-скрипт создаёт файл чуть позже — ждём его появления.
    """
    print(f"> Ожидаем появления файла {path} (до {timeout} сек)...")
    start = time.time()
    while time.time() - start < timeout:
        if path.exists():
            print("> Файл найден.")
            return
        time.sleep(1)
    raise TimeoutError(f"Файл {path} не появился за {timeout} секунд")


def _candidate_bins(project_root: Path) -> dict:
    """пути до потенциальных бинарников (node, npx, ts-node, pnpm) — если пригодится"""
    return {
        "node": project_root / "node_modules" / ".bin" / "node",
        "npx": project_root / "node_modules" / ".bin" / "npx",
        "ts-node": project_root / "node_modules" / ".bin" / "ts-node",
        "pnpm": project_root / "node_modules" / ".bin" / "pnpm",
    }


def _guess_storybook_url(it: Dict[str, Any]) -> Optional[str]:
    """
    Пытаемся угадать ссылку на Storybook по полю storybookPath, если оно есть.
    """
    sb = it.get("storybookPath")
    if not sb:
        return None
    sb = str(sb)
    # пример: "widgets/Text/v1" -> https://storybook.example.com/?path=/story/widgets-text--v1
    # оставляем максимально общий вариант: path=/story/<storybookPath>
    base = os.getenv("STORYBOOK_BASE_URL", "").rstrip("/")
    if not base:
        return None
    return f"{base}/?path=/story/{sb}"


def _format_agents(agents: Any) -> str:
    """
    agents в JSON может быть списком строк или объектов. Нормализуем к строке.
    """
    if agents is None:
        return ""
    if isinstance(agents, str):
        return agents
    if isinstance(agents, list):
        items: List[str] = []
        for a in agents:
            if isinstance(a, str):
                items.append(a)
            elif isinstance(a, dict):
                name = a.get("name") or a.get("id") or a.get("title")
                if name:
                    items.append(str(name))
            else:
                items.append(str(a))
        return ", ".join(items)
    if isinstance(agents, dict):
        # например {"name": "...", "id": "..."}
        parts = []
        for k, v in agents.items():
            parts.append(f"{k}={v}")
        return ", ".join(parts)
    return str(agents)


def _format_bool_icon(value: Optional[bool]) -> str:
    """
    Для DEV/IFT: ✅ / ❌ / пусто, если None.
    """
    if value is True:
        return "✅"
    if value is False:
        return "❌"
    return ""


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
    if widget_presence is None or not hasattr(widget_presence, "check_widget"):
        # импорт не удался — оставляем пустые поля
        out["presence_error"] = f"widget_presence.check_widget недоступен"
        return out

    try:
        presence = widget_presence.check_widget(name, version_int, timeout=timeout)
    except Exception as e:
        out["presence_error"] = f"Ошибка проверки доступности: {e!r}"
        return out

    # Ожидаем, что presence = {"DEV": {"present": bool, "url": str | None}, "IFT": {...}}
    dev_info = presence.get("DEV") or {}
    ift_info = presence.get("IFT") or {}

    out["dev_present"] = bool(dev_info.get("present"))
    out["dev_url"] = dev_info.get("url")

    out["ift_present"] = bool(ift_info.get("present"))
    out["ift_url"] = ift_info.get("url")

    return out


def _load_published_widgets_json(path: Path) -> Dict[str, Dict[str, set]]:
    """
    Читает JSON с опубликованными виджетами и строит словарь:
      { widget_name: { "DEV": {версии}, "IFT": {версии} } }
    Формат файла ожидается как в published-widgets.json.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        print(f"[presence-json] Файл не найден: {path}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"[presence-json] Ошибка чтения {path}: {e}", file=sys.stderr)
        return {}

    if not isinstance(raw, list):
        print(f"[presence-json] Ожидался массив объектов, а пришло: {type(raw)!r}", file=sys.stderr)
        return {}

    result: Dict[str, Dict[str, set]] = {}

    for obj in raw:
        if not isinstance(obj, dict):
            continue
        name = str(obj.get("widget") or "").strip()
        if not name:
            continue

        dev_versions: set = set()
        for rec in obj.get("DEV") or []:
            if isinstance(rec, dict) and rec.get("releaseVersion") is not None:
                try:
                    dev_versions.add(int(rec["releaseVersion"]))
                except Exception:
                    continue

        ift_versions: set = set()
        for rec in obj.get("IFT") or []:
            if isinstance(rec, dict) and rec.get("releaseVersion") is not None:
                try:
                    ift_versions.add(int(rec["releaseVersion"]))
                except Exception:
                    continue

        result[name] = {"DEV": dev_versions, "IFT": ift_versions}

    return result


def _apply_published_presence(it: Dict[str, Any],
                              presence_map: Dict[str, Dict[str, set]]) -> Dict[str, Any]:
    """
    Перезаписывает поля dev_present / ift_present на основе presence_map:
      - dev_present = True/False в зависимости от того,
        входит ли xVersion в DEV-версии из JSON
      - ift_present = True/False аналогично для IFT
    Если данных по виджету/версии нет — элемент остаётся как есть.
    """
    name = str(it.get("name") or "").strip()
    version = it.get("xVersion")

    try:
        version_int = int(version) if version is not None else None
    except Exception:
        version_int = None

    out = dict(it)

    if not name or version_int is None:
        return out

    info = presence_map.get(name)
    if not info:
        return out

    dev_versions = info.get("DEV") or set()
    ift_versions = info.get("IFT") or set()

    if dev_versions:
        out["dev_present"] = version_int in dev_versions
    if ift_versions:
        out["ift_present"] = version_int in ift_versions

    return out


def render_table_html(items: List[Dict[str, Any]]) -> str:
    """
    Рендерим HTML-таблицу.

    Колонки:
      - name
      - xVersion
      - agents
      - display_description
      - Storybook
      - DEV
      - IFT
    """
    headers = [
        "Name",
        "xVersion",
        "Agents",
        "Description",
        "Storybook",
        "DEV",
        "IFT",
    ]

    rows: List[str] = []

    for it in items:
        name = str(it.get("name") or "")
        version = it.get("xVersion")
        agents = _format_agents(it.get("agents"))
        desc = str(it.get("display_description") or it.get("description") or "")

        sb_url = _guess_storybook_url(it)
        if sb_url:
            sb_link = f'<a href="{html.escape(sb_url)}" target="_blank" rel="noopener noreferrer">Story</a>'
        else:
            sb_link = ""

        dev_icon = _format_bool_icon(it.get("dev_present"))
        ift_icon = _format_bool_icon(it.get("ift_present"))

        dev_cell = dev_icon
        ift_cell = ift_icon

        row = "<tr>" + "".join(
            [
                f"<td>{html.escape(str(name))}</td>",
                f"<td>{html.escape(str(version))}</td>",
                f"<td>{html.escape(str(agents))}</td>",
                f"<td>{html.escape(str(desc))}</td>",
                f"<td>{sb_link}</td>",
                f"<td style='text-align:center'>{dev_cell}</td>",
                f"<td style='text-align:center'>{ift_cell}</td>",
            ]
        ) + "</tr>"
        rows.append(row)

    thead = "<thead><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead>"
    tbody = "<tbody>" + "".join(rows) + "</tbody>"
    table = f'<table class="wrapped confluenceTable">{thead}{tbody}</table>'
    return table


def get_confluence_auth() -> ConfluenceAuth:
    base_url = _env_or_raise("CONF_URL")
    user = os.getenv("CONF_USER")
    password = _env_or_raise("CONF_PASS")
    return ConfluenceAuth(base_url=base_url, user=user, password=password)


def _confluence_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
    }


def _get_page(auth: ConfluenceAuth, page_id: int) -> Dict[str, Any]:
    url = f"{auth.base_url}/rest/api/content/{page_id}?expand=body.storage,version,ancestors"
    resp = requests.get(url, auth=(auth.user, auth.password), headers=_confluence_headers())
    if not resp.ok:
        raise RuntimeError(f"Не удалось получить страницу {page_id}: {resp.status_code} {resp.text}")
    return resp.json()


def _update_page_storage(
    auth: ConfluenceAuth,
    page_id: int,
    title: str,
    html_body: str,
    new_version: int,
    ancestors: Optional[List[Dict[str, Any]]] = None,
    message: str = "",
) -> Dict[str, Any]:
    """
    Обновить body.storage у страницы.
    """
    url = f"{auth.base_url}/rest/api/content/{page_id}"
    data: Dict[str, Any] = {
        "id": str(page_id),
        "type": "page",
        "title": title,
        "version": {
            "number": new_version,
            "minorEdit": True,
        },
        "body": {
            "storage": {
                "value": html_body,
                "representation": "storage",
            }
        },
    }
    if ancestors:
        data["ancestors"] = [{"id": str(a["id"])} for a in ancestors]
    if message:
        data["version"]["message"] = message

    resp = requests.put(
        url,
        auth=(auth.user, auth.password),
        headers=_confluence_headers(),
        data=json.dumps(data),
    )
    if not resp.ok:
        raise RuntimeError(f"Не удалось обновить страницу {page_id}: {resp.status_code} {resp.text}")
    return resp.json()


def update_confluence_page(
    conf_url: str,
    auth: ConfluenceAuth,
    page_id: int,
    title: str,
    html_body: str,
    new_version: int,
    ancestors: Optional[List[Dict[str, Any]]] = None,
    message: str = "",
) -> None:
    """
    Высокоуровневая обёртка над _update_page_storage.
    """
    print(f"> Обновляем Confluence pageId={page_id} до версии {new_version}...")
    _update_page_storage(
        auth,
        page_id,
        title,
        html_body,
        new_version,
        ancestors=ancestors,
        message=message,
    )
    print("> Страница успешно обновлена.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Собрать таблицу из widget-meta.json и записать в Confluence")
    ap.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    ap.add_argument("--outdir", required=True, help="Папка вывода (относительно ТЕКУЩЕЙ рабочей директории)")
    ap.add_argument("--outfile", default="widget-meta.json", help="Имя JSON файла (default: widget-meta.json)")
    ap.add_argument("--timeout", type=int, default=300, help="Таймаут запуска TS, сек (default 300)")
    ap.add_argument("--wait", type=int, default=120, help="Таймаут ожидания JSON, сек (default 120)")
    ap.add_argument("--page-id", type=int, default=int(os.getenv("CONF_PAGE_ID", "21609790602")), help="Confluence pageId")
    ap.add_argument(
        "--presence-json",
        help="Путь к JSON с опубликованными виджетами (например published-widgets.json)"
    )
    args = ap.parse_args()

    script_path = Path(args.script).resolve()
    outdir = Path(args.outdir).resolve()
    outfile = outdir / args.outfile

    outdir.mkdir(parents=True, exist_ok=True)

    # 1) запускаем build-meta-from-zod.ts
    json_path = _run_build_script(script_path, outdir, args.outfile, timeout=args.timeout)

    # 2) ждём появления файла (если уже есть, вернётся сразу)
    _wait_for_file(json_path, timeout=args.wait)

    # 3) читаем JSON
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise RuntimeError("Ожидался массив объектов с виджетами.")

    # --- читаем JSON с опубликованными виджетами (DEV/IFT), если он есть ---
    presence_map: Dict[str, Dict[str, set]] = {}

    presence_path: Optional[Path] = None
    if args.presence_json:
        presence_path = Path(args.presence_json).resolve()
    else:
        # по умолчанию пробуем published-widgets.json рядом с outfile
        default_presence = outfile.parent / "published-widgets.json"
        if default_presence.exists():
            presence_path = default_presence

    if presence_path is not None:
        presence_map = _load_published_widgets_json(presence_path)
        print(f"> Загружен файл присутствия виджетов: {presence_path} ({len(presence_map)} записей)")
    else:
        print("> Файл presence-json не указан и published-widgets.json не найден — DEV/IFT будут определяться только стандартной логикой.")

    # --- дополняем данными о доступности (DEV/IFT) и строим таблицу ---
    base_enriched = [_presence_for_item(it) for it in data]

    if presence_map:
        enriched = [_apply_published_presence(it, presence_map) for it in base_enriched]
    else:
        enriched = base_enriched

    table_html = render_table_html(enriched)
    page_html = f"""
<p><strong>Widget meta table</strong> — обновлено: {html.escape(time.strftime('%Y-%m-%d %H:%M:%S'))}</p>
{table_html}
""".strip()

    # --- отправляем в Confluence ---
    conf_url = 'https://confluence.sberbank.ru'
    os.environ["CONF_URL"] = conf_url  # на всякий случай
    auth = get_confluence_auth()

    page_id = int(args.page_id)
    page = _get_page(auth, page_id)
    title = page.get("title") or "Widget meta table"
    version = page.get("version") or {}
    current_version = int(version.get("number") or 1)
    next_version = current_version + 1
    ancestors = page.get("ancestors") or None

    update_confluence_page(
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
