#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
jira_export_all_fields_with_date.py

Выгружает ВСЕ задачи из одного проекта Jira (пространства)
с ограничением по дате создания задач (created).

Фильтр:
    project = KEY
    [AND created >= "YYYY-MM-DD"]
    [AND created <= "YYYY-MM-DD"]

Результат: JSON-файл с сырыми данными по задачам (все поля).
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

# Ограничения по дате создания задач (формат: YYYY-MM-DD)
# Если не нужно ограничение снизу/сверху — оставь None
CREATED_FROM = "2024-01-01"                    # TODO: нижняя граница created (включительно) или None
CREATED_TO   = "2024-12-31"                    # TODO: верхняя граница created (включительно) или None

PAGE_LIMIT = 100                               # размер «страницы» при пагинации

# --- Выходной файл ---
OUTPUT_FILE = f"jira_{JIRA_PROJECT_KEY}_export_all_fields_with_date.json"

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

def build_jql_for_project_with_dates(
    project_key: str,
    created_from: str | None,
    created_to: str | None,
) -> str:
    """
    Собирает JQL с ограничениями по дате created.

    Примеры:
      project = "SHIP"
      project = "SHIP" AND created >= "2024-01-01"
      project = "SHIP" AND created <= "2024-12-31"
      project = "SHIP" AND created >= "2024-01-01" AND created <= "2024-12-31"
    """
    parts = [f'project = "{project_key}"']

    if created_from:
        parts.append(f'created >= "{created_from}"')

    if created_to:
        parts.append(f'created <= "{created_to}"')

    jql = " AND ".join(parts)
    return jql


def fetch_all_issues_for_project(
    jira: Jira,
    project_key: str,
    created_from: str | None,
    created_to: str | None,
) -> List[Dict[str, Any]]:
    """
    Забирает ВСЕ задачи из проекта Jira со всеми полями,
    с ограничением по дате создания (created).
    """
    jql = build_jql_for_project_with_dates(project_key, created_from, created_to)
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

    logging.info(f"Всего задач по фильтру: {len(issues)}")
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
    logging.info("Старт jira_export_all_fields_with_date.py")

    jira = get_jira_client()
    issues = fetch_all_issues_for_project(
        jira,
        JIRA_PROJECT_KEY,
        CREATED_FROM,
        CREATED_TO,
    )

    save_issues_to_json(issues, OUTPUT_FILE)

    logging.info("Готово")


if __name__ == "__main__":
    main()
