import re


def parse_log_line(log_line):
    # Разделение по разделителю "||" для группы 15 и 16, остальные группы разделяются пробелом
    parts = re.split(r' \|\| ', log_line)

    # Обработка каждой части, разделенной по пробелам
    groups = parts[0].split()

    # Если группа 15 и 16 присутствуют в данных, то извлекаем их из части после "||"
    if len(parts) > 1:
        group_15_16 = parts[1].split()
        groups.append(group_15_16[0])  # Группа 15
        groups.append(group_15_16[1])  # Группа 16

    return groups


# Пример использования
log_line = '2024-12-16T11:02:40.175Z POST /api/v1/callback/issuance-confirm HTTP/1.1 500 - via_upstream - - 589 115 607 606 "29.65.65.76,29.64.4.1" "ReactorNetty/0.9.7.RELEASE" "f7a7f957-30fa-9a07-9b28-7ca46a5387f8" "ekp-interaction-service-ift-ssl.apps.ift-terra000008" "127.0.0.1:8080" inbound|8080|| 127.0.0.1:50594 29.64.178.88:8080 29.64.4.1:0'

# Анализ строки лога
parsed_data = parse_log_line(log_line)

# Вывод результата
for i, group in enumerate(parsed_data, 1):
    print(f'Группа {i}: {group}')
