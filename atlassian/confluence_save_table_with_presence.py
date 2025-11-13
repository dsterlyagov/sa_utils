#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Запускает build-meta-from-zod.ts (если доступен раннер), читает JSON из --outdir/--outfile,
строит таблицу (name, xVersion, DEV, IFT, agents, display_description, Storybook) и записывает в Confluence.

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
    --presence-json .\published-widgets.json ^
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
from typing import Any, Dict, List, Optional, Set

# импорт утилиты проверки присутствия виджета на DEV/IFT
try:
    import widget_presence  # из соседнего файла
except Exception as _e_wp:
    widget_presence = None
    _WIDGET_PRESENCE_IMPORT_ERROR = _e_wp
else:
    _WIDGET_PRESENCE_IMPORT_ERROR = None

# -------------------- запуск TS --------------------


def _project_root_from_script(script_path: Path) -> Path:
    """
    Пытаемся найти корень проекта по расположению скрипта:
      widget-store/scripts/build-meta-from-zod.ts
    => корень widget-store
    """
    # build-meta-from-zod.ts обычно лежит в widget-store/scripts
    # корень проекта — родитель scripts
    return script_path.parent.parent


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _detect_module_type(project_root: Path) -> str:
    """esm | cjs | unknown"""
    pkg = _read_json(project_root / "package.json") or {}
    if pkg.get("type") == "module":
        return "esm"
    tsconfig = _read_json(project_root / "tsconfig.json") or {}
    comp = (tsconfig.get("compilerOptions") or {})
    module = (comp.get("module") or "").lower()
    if module.startswith("es"):
        return "esm"
    if module in {"commonjs", "cjs"}:
        return "cjs"
    return "unknown"


def _candidate_bins(project_root: Path) -> dict:
    """пути к бинарям из node_modules/.bin + системные"""
    bin_dir = project_root / "node_modules" / ".bin"
    win = os.name == "nt"

    def variants(name: str) -> List[str]:
        out = []
        if win:
            p = (bin_dir / f"{name}.cmd").resolve()
            if p.exists():
                out.append(str(p))
        p2 = (bin_dir / name).resolve()
        if p2.exists():
            out.append(str(p2))
        # системные
        out.append(name)
        return out

    return {
        "node": variants("node"),
        "tsnode": variants("ts-node"),
        "tsx": variants("tsx"),
        "npx": variants("npx"),
    }


def _run_subprocess(cmd: List[str], cwd: Path, timeout: int) -> None:
    print("> RUN:", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            encoding="utf-8",
            errors="ignore",
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Команда превысила таймаут {timeout} сек: {' '.join(cmd)}")
    if proc.returncode != 0:
        raise RuntimeError(
            f"Команда завершилась с кодом {proc.returncode}: {' '.join(cmd)}\n"
            f"-----\n{proc.stdout}\n-----"
        )
    if proc.stdout:
        print(proc.stdout)


def _maybe_run_ts(script_path: Path, project_root: Path, timeout: int) -> None:
    """Пробует запустить build-meta-from-zod.ts если есть раннер, иначе ничего не делает."""
    if not script_path.exists():
        raise FileNotFoundError(f"TS скрипт не найден: {script_path}")

    bins = _candidate_bins(project_root)
    module_type = _detect_module_type(project_root)
    print(f"> module_type={module_type}")

    # пробуем несколько стратегий
    strategies: List[List[str]] = []

    # 1) ts-node
    for tsnode in bins["tsnode"]:
        strategies.append([tsnode, str(script_path)])

    # 2) tsx
    for tsx in bins["tsx"]:
        strategies.append([tsx, str(script_path)])

    # 3) npx ts-node
    for npx in bins["npx"]:
        strategies.append([npx, "ts-node", str(script_path)])

    # 4) node (если скрипт саморегистрирует ts-node/ts-node/register)
    for node in bins["node"]:
        strategies.append([node, str(script_path)])

    last_error: Optional[Exception] = None
    for cmd in strategies:
        try:
            _run_subprocess(cmd, cwd=project_root, timeout=timeout)
            return
        except Exception as e:
            print(f"! Не удалось запустить {' '.join(cmd)}: {e}")
            last_error = e

    print("! Не удалось запустить TS скрипт, продолжаем, предполагая что JSON уже существует.")
    if last_error:
        print(f"Последняя ошибка: {last_error}")


# -------------------- утилиты по виджетам --------------------


def _extract_display_description(item: Dict[str, Any]) -> str:
    """Безопасно достаем item["display"]["description"] если есть."""
    display = item.get("display") or {}
    if not isinstance(display, dict):
        return ""
    desc = display.get("description")
    if isinstance(desc, str):
        return desc
    return ""


def _agents_list(item: Dict[str, Any]) -> str:
    """Пытаемся вытащить список агентов (если есть)."""
    configs = item.get("configurations")
    if not isinstance(configs, list):
        return ""
    agents: List[str] = []
    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        agent = cfg.get("agent")
        if isinstance(agent, str):
            agents.append(agent)
    return ", ".join(sorted(set(agents)))


def _storybook_link(name: str) -> str:
    """Генерируем ссылку на Storybook по имени виджета."""
    if not name:
        return ""
    # тут лучше подставить ваш реальный URL Storybook
    url = f"https://widget-store.example.com/storybook/?path=/story/{html.escape(name)}"
    return f'<a href="{url}">Storybook</a>'


def _safe(val: Any) -> str:
    if val is None:
        return ""
    return html.escape(str(val))


def _link(url: Optional[str]) -> str:
    if not url:
        return ""
    return f'<a href="{_safe(url)}">{_safe(url)}</a>'


def _build_presence_map(objs: Any) -> Dict[str, Dict[str, Set[int]]]:
    """Построить карту {widget_name: {"DEV": {versions}, "IFT": {versions}}} из JSON.

    Ожидаемый формат JSON (пример):
    [
      {
        "widget": "ask-user-select-answers",
        "DEV": [{"releaseVersion": 24}, ...],
        "IFT": [{"releaseVersion": 21}, ...]
      },
      ...
    ]
    """
    result: Dict[str, Dict[str, Set[int]]] = {}
    if not isinstance(objs, list):
        return result
    for rec in objs:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("widget") or rec.get("name") or "").strip()
        if not name:
            continue

        dev_set: Set[int] = set()
        if isinstance(rec.get("DEV"), list):
            for d in rec["DEV"]:
                if isinstance(d, dict) and "releaseVersion" in d:
                    try:
                        dev_set.add(int(d["releaseVersion"]))
                    except Exception:
                        pass

        ift_set: Set[int] = set()
        if isinstance(rec.get("IFT"), list):
            for d in rec["IFT"]:
                if isinstance(d, dict) and "releaseVersion" in d:
                    try:
                        ift_set.add(int(d["releaseVersion"]))
                    except Exception:
                        pass

        result[name] = {"DEV": dev_set, "IFT": ift_set}
    return result


def _presence_for_item(
    it: Dict[str, Any],
    timeout: float = 8.0,
    presence_map: Optional[Dict[str, Dict[str, Set[int]]]] = None,
) -> Dict[str, Any]:
    """Дополнить элемент полями доступности на DEV/IFT (dev_present, ift_present, dev_url, ift_url).

    Логика:
      * если передан presence_map (из JSON), считаем присутствие по нему;
      * иначе (fallback) используем модуль widget_presence, если он доступен.
    """
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

    # если нет имени или версии — ничего не делаем
    if not name or version_int is None:
        return out

    # 1) сначала пробуем presence_map из JSON
    if presence_map:
        by_widget = presence_map.get(name)
        if by_widget:
            dev_set = by_widget.get("DEV") or set()
            ift_set = by_widget.get("IFT") or set()
            out["dev_present"] = version_int in dev_set
            out["ift_present"] = version_int in ift_set
            # URL-ов в JSON нет, поэтому оставляем как есть
            return out

    # 2) fallback — старый путь через widget_presence
    if widget_presence is None or not hasattr(widget_presence, "check_widget"):
        # импорт не удался — оставляем пустые поля
        out["presence_error"] = "widget_presence unavailable"
        return out
    try:
        res = widget_presence.check_widget(name, version_int, timeout=timeout)  # type: ignore[attr-defined]
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

        def _present_to_cell(val: Any) -> str:
            if val is True:
                return "✔"
            if val is False:
                return "—"
            return ""

        dev_cell = _present_to_cell(dev_present)
        ift_cell = _present_to_cell(ift_present)

        rows.append(
            f"""    <tr>
      <td>{_safe(name)}</td>
      <td>{_safe(xver)}</td>
      <td>{_safe(dev_cell)}</td>
      <td>{_safe(ift_cell)}</td>
      <td>{_safe(agents)}</td>
      <td>{_safe(desc)}</td>
      <td>{link}</td>
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
    url = f"{conf_url}/rest/api/content/{page_id}?expand=version,ancestors"
    headers = {"Accept": "application/json", "Authorization": auth}
    return _http("GET", url, headers)


def confluence_put_storage(
    conf_url: str,
    auth: str,
    page_id: int,
    title: str,
    storage_html: str,
    version: int,
    ancestors: Optional[List[Dict[str, Any]]] = None,
    message: str = "",
) -> Dict[str, Any]:
    url = f"{conf_url}/rest/api/content/{page_id}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": auth,
    }
    data: Dict[str, Any] = {
        "id": str(page_id),
        "type": "page",
        "title": title,
        "version": {"number": version, "message": message or "update via script"},
        "body": {
            "storage": {
                "value": storage_html,
                "representation": "storage",
            }
        },
    }
    if ancestors:
        data["ancestors"] = [{"id": a["id"]} for a in ancestors if "id" in a]
    payload = json.dumps(data).encode("utf-8")
    return _http("PUT", url, headers, payload)


# -------------------- main --------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Собрать таблицу из widget-meta.json и записать в Confluence")
    ap.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    ap.add_argument(
        "--outdir",
        required=True,
        help="Папка вывода (относительно ТЕКУЩЕЙ рабочей директории)",
    )
    ap.add_argument(
        "--outfile",
        default="widget-meta.json",
        help="Имя JSON файла (default: widget-meta.json)",
    )
    ap.add_argument(
        "--presence-json",
        help="Путь к JSON с публикациями виджетов (DEV/IFT)",
    )
    ap.add_argument("--timeout", type=int, default=300, help="Таймаут запуска TS, сек (default 300)")
    ap.add_argument("--wait", type=int, default=120, help="Таймаут ожидания JSON, сек (default 120)")
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

    # ВАЖНО: --outdir интерпретируем относительно ТЕКУЩЕЙ РАБОЧЕЙ ДИРЕКТОРИИ (os.getcwd())
    outdir = (Path(os.getcwd()) / args.outdir).resolve() if not Path(args.outdir).is_absolute() else Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    project_root = _project_root_from_script(script)
    print("> project_root:", project_root)

    # --- запускаем TS (опционально) ---
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

    # --- читаем presence JSON (опционально) ---
    presence_map = None
    if getattr(args, "presence_json", None):
        presence_path = Path(args.presence_json).resolve()
        print(f"> Presence JSON: {presence_path}")
        try:
            with presence_path.open("r", encoding="utf-8") as f:
                presence_raw = json.load(f)
            presence_map = _build_presence_map(presence_raw)
            print(f"> Загружено presence-записей: {len(presence_map)} виджетов")
        except Exception as e:
            print(f"! Предупреждение: не удалось прочитать presence JSON ({presence_path}): {e}", file=sys.stderr)
            presence_map = None

    # --- дополняем данными о доступности (DEV/IFT) и строим таблицу ---
    enriched = [_presence_for_item(it, presence_map=presence_map) for it in data]
    table_html = render_table_html(enriched)
    page_html = f"""
<p><strong>Widget meta table</strong> — обновлено: {html.escape(time.strftime('%Y-%m-%d %H:%M:%S'))}</p>
{table_html}
""".strip()

    # --- отправляем в Confluence ---
    # Если хочешь, можешь вернуть использование ENV-переменных:
    # conf_url = os.getenv("CONF_URL")
    # conf_user = os.getenv("CONF_USER")
    # conf_pass = os.getenv("CONF_PASS")
    conf_url = 'https://confluence.sberbank.ru'
    conf_user = '19060455'
    conf_pass = '19#MOSiad#24'
    page_id = args.page_id
    if not conf_url or not conf_pass:
        raise RuntimeError("Нужно задать ENV: CONF_URL и CONF_PASS (и при Basic — CONF_USER).")

    auth = _auth_header(conf_user, conf_pass)
    page = confluence_get_page(conf_url, auth, page_id)
    title = page.get("title") or f"Page {page_id}"
    ancestors = page.get("ancestors") or []
    next_version = int(page.get("version", {}).get("number", 0)) + 1

    _ = confluence_put_storage(
        conf_url,
        auth,
        page_id,
        title,
        page_html,
        next_version,
        ancestors=ancestors,
        message="Автообновление: таблица (name, xVersion, DEV, IFT, agents, display_description, Storybook)",
    )
    print(f"✅ Обновлено: pageId={page_id} (версия {next_version})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
