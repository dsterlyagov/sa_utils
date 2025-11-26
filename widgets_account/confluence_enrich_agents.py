#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
confluence_enrich_agents.py

Скрипт ТОЛЬКО ДЛЯ CONFLUENCE:

- Читает файл agents_from_jira.json (результат jira_agents_export.py)
- Для каждого агента ищет страницы в Confluence
  (по умолчанию в пространстве DA — цифровой ассистент),
  где упоминается его имя (в заголовке или теле страницы)
- Добавляет к структуре поля:
    "confluence_pages": [ {id, title, url, space_key}, ... ]
    "confluence_spaces": ["DA", ...]
- Сохраняет итог в:
    - agents_result.json
    - agents_result.csv (короткая сводка)
"""

import json
import csv
import logging
from typing import Dict, Any, List

from atlassian import Confluence


# =========================
# НАСТРОЙКИ
# =========================

# ---------- Confluence ----------
CONFLUENCE_URL = "https://confluence.your-domain.com"  # TODO: URL Confluence
CONFLUENCE_USER = "your_login"                         # TODO: логин / почта
CONFLUENCE_TOKEN = "your_token_or_password"            # TODO: токен / пароль

CONFLUENCE_AGENT_SPACE = "DA"  # пространство цифрового ассистента

# Входной файл с агентами из Jira
INPUT_FILE_JIRA_AGENTS = "agents_from_jira.json"

# Выходные файлы
OUTPUT_FILE_AGENTS_JSON = "agents_result.json"
OUTPUT_FILE_AGENTS_CSV = "agents_result.csv"

LOG_LEVEL = logging.INFO


# =========================
# ЛОГГЕР
# =========================

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# =========================
# КЛИЕНТ CONFLUENCE
# =========================

def get_confluence_client() -> Confluence:
    """
    Инициализация клиента Confluence.
    """
    return Confluence(
        url=CONFLUENCE_URL,
        username=CONFLUENCE_USER,
        password=CONFLUENCE_TOKEN,
        verify_ssl=False,
    )


# =========================
# РАБОТА С JSON АГЕНТОВ (ИЗ JIRA)
# =========================

def load_agents_from_json(filename: str) -> Dict[str, Dict[str, Any]]:
    """
    Загружает словарь агентов из JSON-файла.
    """
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)

    logging.info(f"Из файла {filename} загружено агентов: {len(data)}")
    return data


# =========================
# CONFLUENCE: ПОИСК СТРАНИЦ ПО АГЕНТУ
# =========================

def get_agent_confluence_pages_in_space(
    confluence: Confluence,
    agent_name: str,
    space_key: str = CONFLUENCE_AGENT_SPACE,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """
    Ищем страницы в заданном пространстве Confluence (по умолчанию DA),
    в названии или в тексте которых встречается имя агента.
    """
    logging.info(f"Ищу страницы агента '{agent_name}' в пространстве '{space_key}'")

    pages = confluence.get_all_pages_from_space(
        space_key,
        start=0,
        limit=limit,
        status=None,
        expand="body.storage",
        content_type="page",
    )

    result_pages: List[Dict[str, Any]] = []

    for page in pages:
        title = page.get("title", "")
        body = (page.get("body") or {}).get("storage", {}).get("value", "")

        if agent_name and (agent_name in title or agent_name in body):
            page_id = page.get("id")
            result_pages.append({
                "id": page_id,
                "title": title,
                "url": f"{CONFLUENCE_URL}/pages/viewpage.action?pageId={page_id}",
                "space_key": space_key,
            })

    logging.info(
        f"Для агента '{agent_name}' найдено страниц в Confluence ({space_key}): {len(result_pages)}"
    )
    return result_pages


def enrich_agents_with_confluence(
    agents_dict: Dict[str, Dict[str, Any]],
    confluence: Confluence,
) -> Dict[str, Dict[str, Any]]:
    """
    Добавляет к каждому агенту информацию из Confluence:
      - confluence_pages: список страниц
      - confluence_spaces: список ключей пространств (по факту, здесь будет ['DA'] или пусто)
    """
    for agent_name, data in agents_dict.items():
        pages = get_agent_confluence_pages_in_space(
            confluence, agent_name, space_key=CONFLUENCE_AGENT_SPACE
        )
        spaces = sorted({p["space_key"] for p in pages}) if pages else []

        data["confluence_pages"] = pages
        data["confluence_spaces"] = spaces

    logging.info("Данные по Confluence добавлены для всех агентов")
    return agents_dict


# =========================
# ВЫГРУЗКА РЕЗУЛЬТАТОВ
# =========================

def save_agents_to_json(agents_dict: Dict[str, Dict[str, Any]], filename: str) -> None:
    """
    Сохранить расширенный результат в JSON.
    """
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(agents_dict, f, ensure_ascii=False, indent=2)
    logging.info(f"Расширенный результат сохранён в JSON: {filename}")


def save_agents_to_csv(agents_dict: Dict[str, Dict[str, Any]], filename: str) -> None:
    """
    Сохранить базовую сводку по агентам в CSV:
      agent_name, jira_spaces, confluence_spaces, issues_count
    """
    with open(filename, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["agent_name", "jira_spaces", "confluence_spaces", "issues_count"])

        for agent_name, data in agents_dict.items():
            jira_spaces = ",".join(data.get("jira_spaces", []))
            confluence_spaces = ",".join(data.get("confluence_spaces", []))
            issues_count = len(data.get("issues", []))
            writer.writerow([agent_name, jira_spaces, confluence_spaces, issues_count])

    logging.info(f"Сводка по агентам сохранена в CSV: {filename}")


# =========================
# MAIN
# =========================

def main():
    logging.info("Старт скрипта confluence_enrich_agents.py")

    # читаем агентов, собранных из Jira
    agents = load_agents_from_json(INPUT_FILE_JIRA_AGENTS)

    # коннект к Confluence
    confluence = get_confluence_client()

    # обогащаем данными из Confluence
    agents = enrich_agents_with_confluence(agents, confluence)

    # сохраняем результат
    save_agents_to_json(agents, OUTPUT_FILE_AGENTS_JSON)
    save_agents_to_csv(agents, OUTPUT_FILE_AGENTS_CSV)

    logging.info("Готово (Confluence → обогащение → JSON/CSV)")


if __name__ == "__main__":
    main()
