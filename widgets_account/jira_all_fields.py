#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
jira_export_all_fields.py

Выгружает ВСЕ задачи из одного проекта Jira (пространства) БЕЗ ограничений
по типу/статусу и т.п. (только project = KEY) со ВСЕМИ полями.

Результат: JSON-файл с сырыми данными по задачам.
"""

import json
import logging
from typing import List, Dict, Any

from atlassian import Jira


# =========================
# НАСТРОЙКИ
# =========================

# --- Jira-подключение ---
JIRA_URL = "https://jira.your-domain.com"      # TODO: URL твоей Jira
JIRA_USER = "your_login"                       # TODO: логин / почта
JIRA_TOKEN = "your_token_or_password"          # TODO: API token / пароль

# --- Что выгружаем ---
JIRA_PROJECT_KEY = "SHIP"                      # TODO: ключ проекта (пространства)
PAGE_LIMIT = 100                               # размер «страницы» при пагинации

# --- Выходной файл ---
OUTPUT_FILE = f"jira_{JIRA_PROJECT_KEY}_export_all_fields.json"

LOG_LEVEL = logging.INFO


# =========================
# ЛОГГЕР
# =========================

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# =========================
# КЛИЕНТ JIRA
# =========================

def get_jira_client() -> Jira:
    """
    Инициализация клиента Jira.
    """
    return Jira(
        url=JIRA_URL,
        username=JIRA_USER,
        password=JIRA_TOKEN,
        verify_ssl=False,  # при необходимости поставь True
    )


# =========================
# ВЫГРУЗКА ВСЕХ ЗАДАЧ ПРОЕКТА
# =========================

def fetch_all_issues_for_project(jira: Jira, project_key: str) -> List[Dict[str, Any]]:
    """
    Забирает ВСЕ задачи из проекта Jira со всеми полями.

    jql: project = <KEY>
    """
    jql = f'project = "{project_key}"'
    logging.info(f"Используем JQL: {jql}")

    start = 0
    issues: List[Dict[str, Any]] = []

    while True:
        logging.info(f"Запрашиваю задачи: start={start}, limit={PAGE_LIMIT}")

        # fields='*all' -> просим все поля
        result = jira.jql(
            jql,
            start=start,
            limit=PAGE_LIMIT,
            fields="*all",
        )

        batch = result.get("issues", [])
        issues.extend(batch)

        logging.info(f"Получено задач в батче: {len(batch)} (всего: {len(issues)})")

        if len(batch) < PAGE_LIMIT:
            break

        start += PAGE_LIMIT

    logging.info(f"Всего задач в проекте {project_key}: {len(issues)}")
    return issues


# =========================
# СОХРАНЕНИЕ В ФАЙЛ
# =========================

def save_issues_to_json(issues: List[Dict[str, Any]], filename: str) -> None:
    """
    Сохраняет список задач Jira в JSON-файл «как есть» (id, key, fields, ...).
    """
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False, indent=2)

    logging.info(f"Дамп задач сохранён в файл: {filename}")


# =========================
# MAIN
# =========================

def main():
    logging.info("Старт jira_export_all_fields.py")

    jira = get_jira_client()
    issues = fetch_all_issues_for_project(jira, JIRA_PROJECT_KEY)

    save_issues_to_json(issues, OUTPUT_FILE)

    logging.info("Готово")


if __name__ == "__main__":
    main()
