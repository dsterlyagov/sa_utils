#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
    while True:
        if proc.poll() is not None:
            break
        line = proc.stdout.readline() if proc.stdout else ""
        if line:
            print(line.rstrip())
        if time.time() - start > timeout:
            proc.kill()
            raise TimeoutError(f"build-meta-from-zod.ts не завершился за {timeout} секунд")
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
    print(f"> Ожидаем появления файла {path} (до {timeout} сек)...")
    start = time.time()
    while time.time() - start < timeout:
        if path.exists():
            print("> Файл найден.")
            return
        time.sleep(1)
    raise TimeoutError(f"Файл {path} не появился за {timeout} секунд")


def _guess_storybook_url(it: Dict[str, Any]) -> Optional[str]:
    sb = it.get("storybookPath")
    if not sb:
        return None
    sb = str(sb)
    base = os.getenv("STORYBOOK_BASE_URL", "").rstrip("/")
    if not base:
        return None
    return f"{base}/?path=/story/{sb}"


def _format_agents(agents: Any) -> str:
    if agents is None:
        return ""
    if isinstance(agents, str):
        return agents
    if isinstance(agents, list):
        parts = []
        for a in agents:
            if isinstance(a, str):
                parts.append(a)
            elif isinstance(a, dict):
                nm = a.get("name") or a.get("id") or a.get("title")
                if nm:
                    parts.append(str(nm))
            else:
                parts.append(str(a))
        return ", ".join(parts)
    if isinstance(agents, dict):
        return ", ".join(f"{k}={v}" for k, v in agents.items())
    return str(agents)


def _format_bool_icon(value: Optional[bool]) -> str:
    if value is True:
        return "✅"
    if value is False:
        return "❌"
    return ""


def _presence_for_item(it: Dict[str, Any], timeout: float = 8.0) -> Dict[str, Any]:
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
        out["presence_error"] = "widget_presence.check_widget недоступен"
        return out

    try:
        res = widget_presence.check_widget(name, version_int, timeout=timeout)
    except Exception as e:
        out["presence_error"] = f"Ошибка проверки: {e!r}"
        return out

    try:
        dev_info = res.get("DEV") or {}
        ift_info = res.get("IFT") or {}
    except Exception as e:
        out["presence_error"] = f"Некорректный формат presence: {e!r}"
        return out

    out["dev_present"] = bool(dev_info.get("present"))
    out["dev_url"] = dev_info.get("url")

    out["ift_present"] = bool(ift_info.get("present"))
    out["ift_url"] = ift_info.get("url")

    return out


def _presence_for_item_safe(it: Dict[str, Any], timeout: float = 8.0) -> Dict[str, Any]:
    try:
        return _presence_for_item(it, timeout=timeout)
    except Exception as e:
        out = dict(it)
        out.setdefault("dev_present", None)
        out.setdefault("ift_present", None)
        out.setdefault("dev_url", None)
        out.setdefault("ift_url", None)
        out["presence_error"] = f"presence-check failed: {e}"
        return out


def _load_published_widgets_json(path: Path) -> Dict[str, Dict[str, set]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"[presence-json] Ошибка чтения {path}: {e}", file=sys.stderr)
        return {}

    if not isinstance(raw, list):
        print("[presence-json] Ожидался список объектов", file=sys.stderr)
        return {}

    result: Dict[str, Dict[str, set]] = {}

    for o in raw:
        if not isinstance(o, dict):
            continue
        name = str(o.get("widget") or "").strip()
        if not name:
            continue

        dev = set()
        for r in o.get("DEV") or []:
            if isinstance(r, dict) and r.get("releaseVersion") is not None:
                try:
                    dev.add(int(r["releaseVersion"]))
                except:
                    pass

        ift = set()
        for r in o.get("IFT") or []:
            if isinstance(r, dict) and r.get("releaseVersion") is not None:
                try:
                    ift.add(int(r["releaseVersion"]))
                except:
                    pass

        result[name] = {"DEV": dev, "IFT": ift}

    return result


def _apply_published_presence(it: Dict[str, Any], presence_map: Dict[str, Dict[str, set]]) -> Dict[str, Any]:
    name = str(it.get("name") or "").strip()
    version = it.get("xVersion")
    try:
        ver = int(version) if version is not None else None
    except:
        ver = None

    out = dict(it)

    if not name or ver is None:
        return out

    if name not in presence_map:
        return out

    dev = presence_map[name].get("DEV", set())
    ift = presence_map[name].get("IFT", set())

    out["dev_present"] = ver in dev
    out["ift_present"] = ver in ift

    return out


def render_table_html(items: List[Dict[str, Any]]) -> str:
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

        sb = _guess_storybook_url(it)
        if sb:
            sb_link = f'<a href="{html.escape(sb)}" target="_blank">Story</a>'
        else:
            sb_link = ""

        dev = _format_bool_icon(it.get("dev_present"))
        ift = _format_bool_icon(it.get("ift_present"))

        row = (
            "<tr>"
            f"<td>{html.escape(name)}</td>"
            f"<td>{html.escape(str(version))}</td>"
            f"<td>{html.escape(str(agents))}</td>"
            f"<td>{html.escape(desc)}</td>"
            f"<td>{sb_link}</td>"
            f"<td style='text-align:center'>{dev}</td>"
            f"<td style='text-align:center'>{ift}</td>"
            "</tr>"
        )
        rows.append(row)

    thead = "<thead><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead>"
    tbody = "<tbody>" + "".join(rows) + "</tbody>"
    return f'<table class="wrapped confluenceTable">{thead}{tbody}</table>'


def get_confluence_auth() -> ConfluenceAuth:
    base_url = _env_or_raise("CONF_URL")
    user = os.getenv("CONF_USER")
    password = _env_or_raise("CONF_PASS")
    return ConfluenceAuth(base_url=base_url, user=user, password=password)


def _confluence_headers() -> Dict[str, str]:
    return {"Content-Type": "application/json"}


def _get_page(auth: ConfluenceAuth, page_id: int) -> Dict[str, Any]:
    url = f"{auth.base_url}/rest/api/content/{page_id}?expand=body.storage,version,ancestors"
    r = requests.get(url, auth=(auth.user, auth.password), headers=_confluence_headers())
    if not r.ok:
        raise RuntimeError(f"Не удалось получить страницу {page_id}: {r.status_code} {r.text}")
    return r.json()


def _update_page_storage(
    auth: ConfluenceAuth,
    page_id: int,
    title: str,
    html_body: str,
    new_version: int,
    ancestors: Optional[List[Dict[str, Any]]] = None,
    message: str = "",
) -> Dict[str, Any]:
    url = f"{auth.base_url}/rest/api/content/{page_id}"
    data = {
        "id": str(page_id),
        "type": "page",
        "title": title,
        "version": {"number": new_version, "minorEdit": True},
        "body": {"storage": {"value": html_body, "representation": "storage"}},
    }
    if ancestors:
        data["ancestors"] = [{"id": str(a["id"])} for a in ancestors]
    if message:
        data["version"]["message"] = message

    r = requests.put(url, auth=(auth.user, auth.password), headers=_confluence_headers(), data=json.dumps(data))
    if not r.ok:
        raise RuntimeError(f"Не удалось обновить страницу {page_id}: {r.status_code} {r.text}")
    return r.json()


def update_confluence_page(
    conf_url: str,
    auth: ConfluenceAuth,
    page_id: int,
    title: str,
    html_body: str,
    new_version: int,
    ancestors: Optional[List[Dict[str, Any]]] = None,
    message: str = "",
):
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
    ap.add_argument("--script", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--outfile", default="widget-meta.json")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--wait", type=int, default=120)
    ap.add_argument("--page-id", type=int, default=int(os.getenv("CONF_PAGE_ID", "21609790602")))
    ap.add_argument("--presence-json", help="Путь к JSON с опубликованными виджетами")
    args = ap.parse_args()

    script_path = Path(args.script).resolve()
    outdir = Path(args.outdir).resolve()
    outfile = outdir / args.outfile

    outdir.mkdir(parents=True, exist_ok=True)

    json_path = _run_build_script(script_path, outdir, args.outfile, timeout=args.timeout)
    _wait_for_file(json_path, timeout=args.wait)

    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise RuntimeError("Ожидался массив объектов с виджетами.")

    presence_map: Dict[str, Dict[str, set]] = {}

    presence_path: Optional[Path] = None
    if args.presence_json:
        presence_path = Path(args.presence_json).resolve()
    else:
        default_presence = outfile.parent / "published-widgets.json"
        if default_presence.exists():
            presence_path = default_presence

    if presence_path is not None:
        presence_map = _load_published_widgets_json(presence_path)
        print(f"> Загружен файл присутствия виджетов: {presence_path} ({len(presence_map)} записей)")
    else:
        print("> Файл presence-json не указан — DEV/IFT будут определяться только стандартной логикой.")

    base_enriched = [_presence_for_item_safe(it) for it in data]

    if presence_map:
        enriched = [_apply_published_presence(it, presence_map) for it in base_enriched]
    else:
        enriched = base_enriched

    table_html = render_table_html(enriched)
    page_html = f"""
<p><strong>Widget meta table</strong> — обновлено: {html.escape(time.strftime('%Y-%m-%d %H:%M:%S'))}</p>
{table_html}
""".strip()

    os.environ["CONF_URL"] = "https://confluence.sberbank.ru"
    auth = get_confluence_auth()

    page_id = int(args.page_id)
    page = _get_page(auth, page_id)
    title = page.get("title") or "Widget meta table"
    version = int(page.get("version", {}).get("number") or 1)
    ancestors = page.get("ancestors") or None

    update_confluence_page(
        auth.base_url,
        auth,
        page_id,
        title,
        page_html,
        version + 1,
        ancestors=ancestors,
        message="Автообновление: таблица виджетов",
    )

    print(f"✅ Готово! Обновлено pageId={page_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Ошибка:", e, file=sys.stderr)
        sys.exit(1)
