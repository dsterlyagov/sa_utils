import re
from collections import defaultdict

# Путь к файлу с логами
LOG_FILE = "access.log"

# Регулярное выражение для нового формата логов Nginx
LOG_PATTERN = re.compile(
    r'(?P<datetime>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}) (?P<level>\w+) (?P<component>\w+) (?P<message>.+)')


def parse_log_line(line):
    """
    Парсит строку лога с использованием регулярного выражения.
    Возвращает словарь с данными или None, если строка не подходит под шаблон.
    """
    match = LOG_PATTERN.match(line)
    if match:
        return match.groupdict()
    return None


def parse_logs(file_path):
    """
    Парсит лог-файл Nginx построчно и возвращает список словарей с данными.
    """
    parsed_logs = []
    with open(file_path, "r") as file:
        for line in file:
            log_data = parse_log_line(line)
            if log_data:
                parsed_logs.append(log_data)
    return parsed_logs


def analyze_logs(logs):
    """
    Выполняет базовый анализ логов и возвращает статистику.
    """
    stats = {
        "total_logs": len(logs),
        "logs_by_level": defaultdict(int),
        "logs_by_component": defaultdict(int),
        "error_messages": [],
    }

    for log in logs:
        stats["logs_by_level"][log["level"]] += 1
        stats["logs_by_component"][log["component"]] += 1
        if log["level"].lower() == "error":
            stats["error_messages"].append(log["message"])

    return stats


def display_stats(stats):
    """
    Выводит статистику в удобочитаемом виде.
    """
    print(f"Общее количество записей в логе: {stats['total_logs']}")

    print("\nКоличество записей по уровням логирования:")
    for level, count in stats["logs_by_level"].items():
        print(f"  {level}: {count}")

    print("\nКоличество записей по компонентам:")
    for component, count in stats["logs_by_component"].items():
        print(f"  {component}: {count}")

    print("\nСписок ошибок:")
    for message in stats["error_messages"]:
        print(f"  {message}")


if __name__ == "__main__":
    # Парсим лог-файл
    logs = parse_logs(LOG_FILE)

    # Анализируем логи
    stats = analyze_logs(logs)

    # Выводим статистику
    display_stats(stats)
