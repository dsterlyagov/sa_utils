#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
jira_issue_to_json.py

По идентификатору (ключу) задачи Jira (например, SHIP-123)
получает полный JSON этой задачи и сохраняет его в файл.

Пример запуска:
    python jira_issue_to_json.py SHIP-123
    python jira_issue_to_json.py SHIP-123 my_issue.json
"""

import sys
import json
import logging
from atlassian import Jira


# =========================
# НАСТРОЙКИ ПОДКЛЮЧЕНИЯ
# =========================

JIRA_URL = "https://jira.your-domain.com"   # TODO: твой URL Jira
JIRA_USER = "your_login"                    # TODO: логин / почта
JIRA_TOKEN = "your_token_or_password"       # TODO: API token / пароль

LOG_LEVEL = logging.INFO


logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def get_jira_client() -> Jira:
    """
    Инициализация клиента Jira.
    """
    return Jira(
        url=JIRA_URL,
        username=JIRA_USER,
        password=JIRA_TOKEN,
        verify_ssl=False,  # при необходимости можно включить True
    )


def fetch_issue_json(jira: Jira, issue_key: str) -> dict:
    """
    Получает JSON задачи по её ключу.

    fields='*all'  -> все поля
    expand=...     -> при необходимости можно расширить (changelog, renderedFields, ...)
    """
    logging.info(f"Запрашиваю задачу {issue_key}")
    issue = jira.issue(
        issue_key,
        fields="*all",
        expand="changelog,renderedFields"  # можно убрать/добавить по желанию
    )
    return issue


def save_json(data: dict, filename: str) -> None:
    """
    Сохраняет JSON в файл.
    """
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logging.info(f"JSON задачи сохранён в файл: {filename}")


def main():
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python jira_issue_to_json.py ISSUE_KEY [output.json]")
        print("Пример:")
        print("  python jira_issue_to_json.py SHIP-123")
        sys.exit(1)

    issue_key = sys.argv[1]
    if len(sys.argv) >= 3:
        output_file = sys.argv[2]
    else:
        output_file = f"issue_{issue_key}.json"

    jira = get_jira_client()
    issue_json = fetch_issue_json(jira, issue_key)
    save_json(issue_json, output_file)


if __name__ == "__main__":
    main()
