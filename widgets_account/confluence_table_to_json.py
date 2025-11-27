#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
confluence_table_to_json.py

По ID страницы Confluence:
  - забирает XHTML (body.storage)
  - находит таблицы <table> (даже внутри визуальных компонентов / макросов)
  - конвертирует их в JSON
  - сохраняет в файл confluence_tables.json

Формат JSON:
[
  {
    "table_index": 0,
    "rows": [
      { "Колонка1": "значение", "Колонка2": "значение", ... },
      ...
    ]
  },
  ...
]
"""

import json
import logging
from typing import List, Dict, Any

from atlassian import Confluence
from bs4 import BeautifulSoup  # pip install beautifulsoup4


# =========================
# НАСТРОЙКИ
# =========================

CONFLUENCE_URL = "https://confluence.your-domain.com"  # TODO: твой URL Confluence
CONFLUENCE_USER = "your_login"                         # TODO: логин / почта
CONFLUENCE_TOKEN = "your_token_or_password"            # TODO: токен / пароль

PAGE_ID = "123456789"                                  # TODO: ID нужной страницы

OUTPUT_FILE = "confluence_tables.json"

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
        verify_ssl=False,  # при необходимости можно включить True
    )


# =========================
# ЗАБОР СТРАНИЦЫ ИЗ CONFLUENCE
# =========================

def get_page_storage_html(confluence: Confluence, page_id: str) -> str:
    """
    Получить HTML (storage формат) страницы по ID.
    """
    logging.info(f"Запрашиваю страницу {page_id} с expand=body.storage")

    page = confluence.get_page_by_id(page_id, expand="body.storage")
    body = (page.get("body") or {}).get("storage", {})
    value = body.get("value", "")

    if not value:
        logging.warning("У страницы нет body.storage.value или оно пустое")

    return value


# =========================
# ПАРСИНГ ТАБЛИЦ И КОНВЕРТАЦИЯ В JSON
# =========================

def extract_tables_as_json(html: str) -> List[Dict[str, Any]]:
    """
    Из HTML (storage) достаёт все <table> и превращает их в JSON-представление.

    Логика:
      - первая строка таблицы считается шапкой (th или td)
      - остальные строки -> объекты с ключами из шапки
    """
    soup = BeautifulSoup(html, "html.parser")

    tables = soup.find_all("table")
    logging.info(f"Найдено таблиц: {len(tables)}")

    tables_json: List[Dict[str, Any]] = []

    for idx, table in enumerate(tables):
        logging.info(f"Обрабатываю таблицу #{idx}")

        rows = table.find_all("tr")
        if not rows:
            logging.info(f"Таблица #{idx} пустая, пропускаю")
            continue

        # --- шапка таблицы ---
        header_cells = rows[0].find_all(["th", "td"])
        headers = [cell.get_text(strip=True) for cell in header_cells]

        # если шапка пустая, сгенерируем имена колонок
        if not any(headers):
            headers = [f"col_{i+1}" for i in range(len(header_cells))]

        logging.info(f"Таблица #{idx} заголовки: {headers}")

        data_rows = []

        # --- данные таблицы ---
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            # пропускаем полностью пустые строки
            if not cells:
                continue

            cell_values = [cell.get_text(strip=True) for cell in cells]

            # если в строке столбцов меньше/больше чем в шапке — подгоним
            if len(cell_values) < len(headers):
                cell_values += [""] * (len(headers) - len(cell_values))
            elif len(cell_values) > len(headers):
                cell_values = cell_values[:len(headers)]

            row_obj = dict(zip(headers, cell_values))
            data_rows.append(row_obj)

        tables_json.append({
            "table_index": idx,
            "rows": data_rows,
        })

    return tables_json


# =========================
# СОХРАНЕНИЕ В ФАЙЛ
# =========================

def save_json(data: Any, filename: str) -> None:
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logging.info(f"JSON сохранён в файл: {filename}")


# =========================
# MAIN
# =========================

def main():
    logging.info("Старт confluence_table_to_json.py")

    confluence = get_confluence_client()

    html = get_page_storage_html(confluence, PAGE_ID)

    tables_json = extract_tables_as_json(html)

    save_json(tables_json, OUTPUT_FILE)

    logging.info("Готово")


if __name__ == "__main__":
    main()
