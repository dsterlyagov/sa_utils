import os
import re


def split_java_method_into_blocks(java_code, method_name, output_dir):
    """
    Разбивает метод на блоки, разделяя их по `if` и другим значимым конструкциям.

    :param java_code: Строки с кодом Java.
    :param method_name: Имя метода, который нужно обработать.
    :param output_dir: Путь к папке для сохранения блоков.
    """
    # Убедимся, что директория существует
    os.makedirs(output_dir, exist_ok=True)

    # Регулярное выражение для поиска начала метода
    method_start_pattern = re.compile(rf'\s*public.*\s+{re.escape(method_name)}\s*\(.*\)\s*\{{')
    in_method = False
    current_block = []
    block_counter = 1

    # Разделяем строки кода на блоки
    for line in java_code:
        if method_start_pattern.match(line):  # Начало метода
            in_method = True

        if in_method:
            current_block.append(line)
            # Если встретили `if` или закрывающую скобку `}`, завершаем текущий блок
            if re.match(r'\s*if\b', line) or line.strip() == '}':
                save_block(output_dir, method_name, block_counter, current_block)
                block_counter += 1
                current_block = []

    # Если остались строки в последнем блоке, сохраняем их
    if current_block:
        save_block(output_dir, method_name, block_counter, current_block)


def save_block(output_dir, method_name, block_counter, block_lines):
    """
    Сохраняет блок в файл.

    :param output_dir: Директория для сохранения блоков.
    :param method_name: Имя метода.
    :param block_counter: Номер текущего блока.
    :param block_lines: Строки блока.
    """
    file_name = os.path.join(output_dir, f"{method_name}_block_{block_counter}.java")
    with open(file_name, 'w') as f:
        f.writelines(block_lines)
    print(f"Блок {block_counter} сохранён в файл: {file_name}")


# Пример использования
java_code_path = "Service.java"  # Файл с классом
method_name = "businessLogic"  # Метод для разбивки
output_dir = "output_blocks"  # Директория для сохранения

# Читаем исходный код Java
with open(java_code_path, 'r') as java_file:
    java_code_lines = java_file.readlines()

# Разбиваем метод на блоки
split_java_method_into_blocks(java_code_lines, method_name, output_dir)
