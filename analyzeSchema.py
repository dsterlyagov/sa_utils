import json
import pandas as pd
from typing import Dict, List


def load_json_schema(file_path: str) -> Dict:
    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)


def extract_properties(schema: Dict, path: str = "") -> List[Dict[str, str]]:
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))
    differences = []

    for field, definition in properties.items():
        field_path = f"{path}.{field}" if path else field
        field_type = definition.get("type", "не указан")
        is_required = "Да" if field in required_fields else "Нет"

        differences.append({"Поле": field_path, "Тип": field_type, "Обязательное": is_required})

        if definition.get("type") == "object":
            differences.extend(extract_properties(definition, field_path))

    return differences


def compare_schemas(base_schema: Dict, consumer_schema: Dict) -> List[Dict[str, str]]:
    base_structure = {entry["Поле"]: entry for entry in extract_properties(base_schema)}
    consumer_structure = {entry["Поле"]: entry for entry in extract_properties(consumer_schema)}

    differences = []

    all_fields = set(base_structure.keys()).union(set(consumer_structure.keys()))

    for field in all_fields:
        base_entry = base_structure.get(field)
        consumer_entry = consumer_structure.get(field)

        if base_entry is None:
            differences.append({"Поле": field, "Изменение": "Добавлено", "Тип": consumer_entry["Тип"],
                                "Обязательное": consumer_entry["Обязательное"]})
        elif consumer_entry is None:
            differences.append({"Поле": field, "Изменение": "Удалено", "Тип": base_entry["Тип"],
                                "Обязательное": base_entry["Обязательное"]})
        else:
            if base_entry["Тип"] != consumer_entry["Тип"]:
                differences.append({"Поле": field, "Изменение": "Изменен тип",
                                    "Тип": f"было: {base_entry['Тип']}, стало: {consumer_entry['Тип']}",
                                    "Обязательное": consumer_entry["Обязательное"]})
            if base_entry["Обязательное"] != consumer_entry["Обязательное"]:
                differences.append({"Поле": field, "Изменение": "Изменена обязательность", "Тип": consumer_entry["Тип"],
                                    "Обязательное": f"было: {base_entry['Обязательное']}, стало: {consumer_entry['Обязательное']}"})

    return differences


if __name__ == "__main__":
    base_schema = load_json_schema("base_schema.json")
    consumer_schema = load_json_schema("consumer_schema.json")

    diff = compare_schemas(base_schema, consumer_schema)

    df = pd.DataFrame(diff)
    print(df)
