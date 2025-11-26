#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
jira_agents_export.py

Скрипт ТОЛЬКО ДЛЯ JIRA:

- Подключается к Jira
- Забирает инициативы проекта SHIP по заданному JQL:
    project = SHIP
    AND type = "Инициатива"
    AND status != Cancelled
    AND summary !~ "Minor*"
    AND summary !~ "HF*"
    AND summary !~ "Major*"
- Для каждой задачи вытаскивает продуктового и доменного агентa
- Строит структуру по агентам:
    {
      "ФИО агента": {
        "jira_spaces": ["SHIP", ...],
        "issues": [ {...}, ... ]
      },
      ...
    }
- Сохраняет результат в JSON-файл: agents_from_jira.json
"""

import json
import logging
from collections import defaultdict
from typing import Dict, Any, List

from atlassian import Jira


# =========================
# НАСТРОЙКИ
# =========================

# ---------- Jira ----------
JIRA_URL = "https://jira.your-domain.com"   # TODO: URL Jira
JIRA_USER = "your_login"                    # TODO: логин / почта
JIRA_TOKEN = "your_token_or_password"       # TODO: токен / пароль

JIRA_PROJECT_KEY = "SHIP"

# ID кастомных полей для продуктового и доменного агентов
JIRA_PRODUCT_AGENT_FIELD = "customfield_XXXX1"  # TODO: id поля "Продуктовый агент"
JIRA_DOMAIN_AGENT_FIELD = "customfield_XXXX2"   # TODO: id поля "Доменный агент"

# Пространства Jira, которые нужно исключить (по ключу проекта)
EXCLUDED_JIRA_SPACES = {"DEVOPSDA", "CARUN", "GIGAUSAGE"}

# Лимит задач за один запрос
JIRA_PAGE_LIMIT = 100

# Выходной файл
OUTPUT_FILE_JIRA_AGENTS = "agents_from_jira.json"

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
        verify_ssl=False,  # при необходимости можно поставить True
    )


# =========================
# JIRA: ПОЛУЧЕНИЕ ИНИЦИАТИВ
# =========================

def fetch_all_issues_by_jql(jira: Jira, jql: str, limit: int = JIRA_PAGE_LIMIT) -> List[Dict[str, Any]]:
    """
    Пагинация по JQL для получения всех задач.
    """
    start = 0
    issues: List[Dict[str, Any]] = []

    while True:
        logging.info(f"Выполняю JQL (start={start}, limit={limit})")
        result = jira.jql(jql, limit=limit, start=start)
        batch = result.get("issues", [])
        issues.extend(batch)

        if len(batch) < limit:
            break

        start += limit

    logging.info(f"Всего получено задач: {len(issues)}")
    return issues


def get_jira_issues(jira: Jira, project_key: str = JIRA_PROJECT_KEY) -> List[Dict[str, Any]]:
    """
    Возвращает список задач (инициатив) по заданным критериям,
    включая продуктового и доменного агентов.
    """
    jql = (
        f'project = {project_key} '
        f'AND type = "Инициатива" '
        f'AND status != Cancelled '
        f'AND summary !~ "Minor*" '
        f'AND summary !~ "HF*" '
        f'AND summary !~ "Major*"'
    )

    logging.info(f"Используем JQL: {jql}")
    raw_issues = fetch_all_issues_by_jql(jira, jql=jql)

    issues_data: List[Dict[str, Any]] = []

    for issue in raw_issues:
        fields = issue.get("fields", {})

        product_agent = fields.get(JIRA_PRODUCT_AGENT_FIELD)
        domain_agent = fields.get(JIRA_DOMAIN_AGENT_FIELD)

        # Приводим агента к строке (обычно пользователь — это dict)
        def _user_to_name(val):
            if isinstance(val, dict):
                return val.get("displayName") or val.get("name") or val.get("key")
            return val

        issue_dict = {
            "key": issue.get("key"),
            "summary": fields.get("summary"),
            "issue_type": (fields.get("issuetype") or {}).get("name"),
            "status": (fields.get("status") or {}).get("name"),
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "project": (fields.get("project") or {}).get("key"),

            "product_agent": _user_to_name(product_agent),
            "domain_agent": _user_to_name(domain_agent),
        }

        issues_data.append(issue_dict)

    logging.info(f"Подготовлено задач с нужными полями: {len(issues_data)}")
    return issues_data


# =========================
# POST-PROCESS: ПРОСТРАНСТВА JIRA ДЛЯ АГЕНТОВ
# =========================

def build_agent_jira_spaces(issues_data: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    На вход: список задач из get_jira_issues.
    На выход: структура вида:
    {
      "ФИО агентa": {
        "jira_spaces": ["SHIP", ...],
        "issues": [ {...}, ... ]
      },
      ...
    }
    """
    agents: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"jira_spaces": set(), "issues": []})

    for issue in issues_data:
        project_key = issue.get("project")
        if not project_key:
            continue

        # исключаем специальные пространства
        if project_key in EXCLUDED_JIRA_SPACES:
            continue

        # Пробегаем по двум типам агентов
        for field_name in ("product_agent", "domain_agent"):
            agent_name = issue.get(field_name)
            if not agent_name:
                continue

            agents[agent_name]["jira_spaces"].add(project_key)
            agents[agent_name]["issues"].append(issue)

    # конвертируем множества в списки
    result: Dict[str, Dict[str, Any]] = {}
    for agent_name, data in agents.items():
        result[agent_name] = {
            "jira_spaces": sorted(list(data["jira_spaces"])),
            "issues": data["issues"],
        }

    logging.info(f"Найдено агентов (по Jira): {len(result)}")
    return result


# =========================
# ВЫГРУЗКА В JSON
# =========================

def save_agents_to_json(agents_dict: Dict[str, Dict[str, Any]], filename: str) -> None:
    """
    Сохранить результат в JSON.
    """
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(agents_dict, f, ensure_ascii=False, indent=2)
    logging.info(f"Результат сохранён в JSON: {filename}")


# =========================
# MAIN
# =========================

def main():
    logging.info("Старт скрипта jira_agents_export.py")

    jira = get_jira_client()

    issues_data = get_jira_issues(jira, project_key=JIRA_PROJECT_KEY)
    agents = build_agent_jira_spaces(issues_data)

    save_agents_to_json(agents, OUTPUT_FILE_JIRA_AGENTS)

    logging.info("Готово (Jira → JSON)")


if __name__ == "__main__":
    main()
