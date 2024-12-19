import xmlschema
import json


def xsd_to_json_schema(xsd_file_path, json_schema_file_path):
    """
    Конвертирует XSD-схему в JSON-схему.

    :param xsd_file_path: Путь к XSD-файлу.
    :param json_schema_file_path: Путь для сохранения JSON Schema.
    """
    try:
        # Загружаем XSD-схему
        xsd_schema = xmlschema.XMLSchema(xsd_file_path)

        # Преобразуем XSD в словарь
        json_schema = xsd_schema.to_dict()

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
