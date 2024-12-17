import re

# Исходные данные (замените на свой текст)
log_data = """
[2024-12-16T10:22:17.970Z]  "GET /fluent-controller/metrics/prometheus?kubernetes-pods=true HTTP/1.1"
[2024-12-16T10:22:33.668Z]  "GET /actuator/prometheus?kubernetes-pods=true HTTP/1.1"
[2024-12-16T10:22:47.971Z]  "GET /fluent-controller/metrics/prometheus?kubernetes-pods=true HTTP/1.1"
[2024-12-16T10:22:58.668Z]  "GET /actuator/prometheus?kubernetes-pods=true HTTP/1.1"
[2024-12-16T10:23:21.502Z]  "POST /engine-rest/external-task/fetchAndLock HTTP/1.1"
"""

# Регулярное выражение для парсинга данных
log_pattern = r'\[(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2}\.\d{3})Z\]\s+"(GET|POST)\s+([^"]+)\s+HTTP'

# Находим все совпадения
matches = re.findall(log_pattern, log_data)

# Вывод результатов
print("Дата       | Время       | Метод | URL")
print("-------------------------------------------")
for match in matches:
    date, time, method, url = match
    print(f"{date} | {time} | {method} | {url}")
