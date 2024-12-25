from lxml import etree

def parse_xsd_to_dict(xsd_file_path):
    """
    Парсит XSD-схему и преобразует её в Python dict с использованием lxml.
    """
    def parse_element(element):
        """
        Рекурсивно обрабатывает элементы XSD и преобразует их в словарь.
        """
        result = {}
        for child in element:
            tag = etree.QName(child.tag).localname  # Извлекаем имя тега без пространства имен
            if tag == "element":
                name = child.get("name")
                element_data = {
                    "type": child.get("type"),
                    "minOccurs": child.get("minOccurs"),
                    "maxOccurs": child.get("maxOccurs"),
                    "annotation": child.find(".//xs:documentation", namespaces=namespaces)
                }
                if element_data["annotation"] is not None:
                    element_data["annotation"] = element_data["annotation"].text
                result[name] = element_data
            elif tag == "complexType":
                complex_type_name = child.get("name")
                result[complex_type_name] = parse_element(child)
            elif tag == "sequence":
                result.update(parse_element(child))
        return result

    try:
        # Парсим XSD файл
        tree = etree.parse(xsd_file_path)
        root = tree.getroot()

        # Пространства имен
        global namespaces
        namespaces = {"xs": "http://www.w3.org/2001/XMLSchema"}

        # Начинаем с корневого элемента схемы
        parsed_data = parse_element(root)

        return parsed_data
    except Exception as e:
        print(f"Ошибка при парсинге XSD: {e}")
        return None


# Пример использования
xsd_file = "example.xsd"  # Укажите путь к вашему XSD-файлу
schema_dict = parse_xsd_to_dict(xsd_file)

if schema_dict:
    print("Результат парсинга XSD в Python dict:")
    print(schema_dict)
