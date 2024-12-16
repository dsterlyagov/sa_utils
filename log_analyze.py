from pyloganalyzer.log_analyzer import LogAnalyzer

# Путь к файлу с логами
LOG_FILE = "access.log"

def analyze_logs(file_path):
    """
    Парсит и анализирует лог-файл Nginx с использованием PyLogAnalyzer.
    Возвращает статистику по запросам.
    """
    analyzer = LogAnalyzer(log_file=file_path, format_name="nginx")
    logs = analyzer.parse_logs()

    stats = {
        "total_requests": len(logs),
        "requests_by_status": {},
        "requests_by_ip": {},
        "top_requested_urls": {},
    }

    for log in logs:
        status = log.get("status")
        ip = log.get("remote_addr")
        url = log.get("request_uri")

        stats["requests_by_status"][status] = stats["requests_by_status"].get(status, 0) + 1
        stats["requests_by_ip"][ip] = stats["requests_by_ip"].get(ip, 0) + 1
        stats["top_requested_urls"][url] = stats["top_requested_urls"].get(url, 0) + 1

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
    # Анализируем логи
    stats = analyze_logs(LOG_FILE)

    # Выводим статистику
    display_stats(stats)
