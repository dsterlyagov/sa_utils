#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Скрипт для получения всех кастомных полей Jira
и сохранения их в JSON-файл.
"""

from atlassian import Jira
import json
import logging

# ============= НАСТРОЙКИ =============
JIRA_URL = "https://jira.your-domain.com"     # TODO: URL Jira
JIRA_USER = "your_login"                       # TODO: логин
JIRA_TOKEN = "your_token"                      # TODO: API токен / пароль

OUTPUT_FILE = "jira_custom_fields.json"
# =====================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def get_jira_client():
    return Jira(
        url=JIRA_URL,
        username=JIRA_USER,
        password=JIRA_TOKEN,
        verify_ssl=False
    )


def fetch_custom_fields(jira: Jira):
    """
    Получает ВСЕ поля Jira → фильтрует только customfield_xxxxx.
    """
    logging.info("Запрашиваю список всех полей Jira...")

    fields = jira.get("/rest/api/2/field")

    logging.info(f"Получено полей: {len(fields)}")

    custom_fields = [
        {
            "id": f.get("id"),
            "name": f.get("name"),
            "schema": f.get("schema", {})
        }
        for f in fields
        if f.get("id", "").startswith("customfield_")
    ]

    logging.info(f"Найдено кастомных полей: {len(custom_fields)}")

    return custom_fields


def save_to_json(data, filename):
    """
    Сохраняет список полей в JSON.
    """
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logging.info(f"JSON сохранён в файл: {filename}")


def print_summary(custom_fields):
    print("\n================ КАСТОМНЫЕ ПОЛЯ ================\n")
    for f in custom_fields:
        print(f"ID:   {f['id']}")
        print(f"Имя:  {f['name']}")
        print(f"Тип:  {(f['schema'] or {}).get('type')}")
        print("-" * 60)


def main():
    jira = get_jira_client()

    custom_fields = fetch_custom_fields(jira)

    save_to_json(custom_fields, OUTPUT_FILE)

    print_summary(custom_fields)

    logging.info("Готово!")


if __name__ == "__main__":
    main()
