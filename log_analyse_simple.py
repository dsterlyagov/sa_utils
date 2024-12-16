import re
from collections import defaultdict

# Путь к файлу с логами
LOG_FILE = "access.log"

# Регулярное выражение для парсинга логов Nginx в формате "combined"
LOG_PATTERN = re.compile(r'(?P<ip>[\d\.]+) - - \[(?P<datetime>.+?)\] "(?P<method>[A-Z]+) (?P<url>.+?) (?P<protocol>.+?)" (?P<status>\d{3}) (?P<size>\d+|-) "(?P<referrer>.+?)" "(?P<user_agent>.+?)"')

def parse_log_line(line):
    """
    Парсит строку лога с использованием регулярного выражения.
    Возвращает словарь с данными или None, если строка не подходит под шаблон.
    """
    match = LOG_PATTERN.match(line)
    if match:
        data = match.groupdict()
        data["size"] = int(data["size"]) if data["size"].isdigit() else 0
        return data
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
        "total_requests": len(logs),
        "requests_by_status": defaultdict(int),
        "requests_by_ip": defaultdict(int),
        "top_requested_urls": defaultdict(int),
    }

    for log in logs:
        stats["requests_by_status"][log["status"]] += 1
        stats["requests_by_ip"][log["ip"]] += 1
        stats["top_requested_urls"][log["url"]] += 1

    # Сортируем топовые URL по частоте запросов
    stats["top_requested_urls"] = sorted(stats["top_requested_urls"].items(), key=lambda x: x[1], reverse=True)[:10]
    return stats

def display_stats(stats):
    """
    Выводит статистику в удобочитаемом виде.
    """
    print(f"Общее количество запросов: {stats['total_requests']}")

    print("\nЗапросы по статусам HTTP:")
    for status, count in stats["requests_by_status"].items():
        print(f"  {status}: {count}")

    print("\nТоп 10 IP-адресов:")
    for ip, count in sorted(stats["requests_by_ip"].items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  {ip}: {count}")

    print("\nТоп 10 запрашиваемых URL:")
    for url, count in stats["top_requested_urls"]:
        print(f"  {url}: {count}")

if __name__ == "__main__":
    # Парсим лог-файл
    logs = parse_logs(LOG_FILE)

    # Анализируем логи
    stats = analyze_logs(logs)

    # Выводим статистику
    display_stats(stats)
