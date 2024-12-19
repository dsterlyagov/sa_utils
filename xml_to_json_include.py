import xmlschema
import json
import os


def load_and_include_schemas(xsd_file_path, base_dir=None):
    """
    Загружает XSD-схему и все связанные с ней схемы (через include и import).

    :param xsd_file_path: Путь к XSD файлу.
    :param base_dir: Директория для поиска зависимых файлов.
    :return: Объект XMLSchema.
    """
    # Если base_dir не передан, то используем директорию файла
    if base_dir is None:
        base_dir = os.path.dirname(xsd_file_path)

    # Загружаем основную схему
    schema = xmlschema.XMLSchema(xsd_file_path)

    # Рекурсивно загружаем все схемы, которые подключаются через include и import
    for include in schema.includes:
        include_path = os.path.join(base_dir, include)
        if not os.path.isabs(include_path):
            include_path = os.path.abspath(include_path)
        schema.include(include_path)

    return schema


def xsd_to_json_schema(xsd_file_path, json_schema_file_path):
    """
    Конвертирует XSD-схему и все включенные схемы в JSON-схему.

    :param xsd_file_path: Путь к XSD-файлу.
    :param json_schema_file_path: Путь для сохранения JSON Schema.
    """
    try:
        # Загружаем XSD-схему и все связанные схемы
        schema = load_and_include_schemas(xsd_file_path)

        # Преобразуем XSD в словарь
        json_schema = schema.to_dict()

        # Сохраняем результат в файл JSON Schema
        with open(json_schema_file_path, 'w', encoding='utf-8') as json_file:
            json.dump(json_schema, json_file, indent=4, ensure_ascii=False)

        print(f"JSON Schema успешно сохранена в '{json_schema_file_path}'")
    except Exception as e:
        print(f"Ошибка при конверсии: {e}")


# Пример использования
xsd_file = "example.xsd"  # Укажите путь к вашему XSD-файлу
json_schema_file = "example.json"  # Укажите, где сохранить JSON Schema
xsd_to_json_schema(xsd_file, json_schema_file)
