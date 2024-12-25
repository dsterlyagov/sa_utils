from lxml import etree
import pandas as pd

def xsd_to_dataframe(xsd_file_path):
    """
    Парсит XSD-схему и преобразует её в pandas DataFrame.
    """
    def parse_element(element, parent_name=""):
        """
        Рекурсивно обрабатывает элементы XSD и собирает данные для DataFrame.
        """
        rows = []
        for child in element:
            tag = etree.QName(child.tag).localname  # Извлекаем имя тега без пространства имен
            if tag == "element":
                name = child.get("name")
                element_data = {
                    "Parent": parent_name,
                    "Element": name,
                    "Type": child.get("type"),
                    "MinOccurs": child.get("minOccurs", "1"),  # По умолчанию 1
                    "MaxOccurs": child.get("maxOccurs", "1"),  # По умолчанию 1
                    "Annotation": child.find(".//xs:documentation", namespaces=namespaces)
                }
                if element_data["Annotation"] is not None:
                    element_data["Annotation"] = element_data["Annotation"].text.strip()
                rows.append(element_data)
            elif tag in {"complexType", "sequence"}:
                # Обрабатываем вложенные элементы
                rows.extend(parse_element(child, parent_name))
        return rows

    try:
        # Парсим XSD файл
        tree = etree.parse(xsd_file_path)
        root = tree.getroot()

        # Пространства имен
        global namespaces
        namespaces = {"xs": "http://www.w3.org/2001/XMLSchema"}

        # Парсим элементы
        parsed_data = parse_element(root)

        # Создаем DataFrame
        df = pd.DataFrame(parsed_data, columns=["Parent", "Element", "Type", "MinOccurs", "MaxOccurs", "Annotation"])

        return df
    except Exception as e:
        print(f"Ошибка при парсинге XSD: {e}")
        return None


# Пример использования
xsd_file = "example.xsd"  # Укажите путь к вашему XSD-файлу
df = xsd_to_dataframe(xsd_file)

if df is not None:
    print("Результат преобразования XSD в DataFrame:")
    print(df)
