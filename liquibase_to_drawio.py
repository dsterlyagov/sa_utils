import xml.etree.ElementTree as ET
import uuid


def generate_id():
    """Генерация уникального ID для элементов Draw.io"""
    return str(uuid.uuid4())


def parse_liquibase(file_path):
    """Парсинг Liquibase XML и извлечение таблиц, колонок и связей"""
    tree = ET.parse(file_path)
    root = tree.getroot()

    # Пространство имен Liquibase (если есть)
    ns = {'lb': 'http://www.liquibase.org/xml/ns/dbchangelog'}

    tables = {}
    relationships = []

    # Парсим таблицы
    for change_set in root.findall("lb:changeSet", ns):
        for create_table in change_set.findall("lb:createTable", ns):
            table_name = create_table.attrib['tableName']
            tables[table_name] = []

            # Парсим колонки
            for column in create_table.findall("lb:column", ns):
                column_name = column.attrib['name']
                column_type = column.attrib.get('type', 'UNKNOWN')
                tables[table_name].append((column_name, column_type))

        # Парсим внешние ключи
        for add_foreign_key in change_set.findall("lb:addForeignKeyConstraint", ns):
            fk_table = add_foreign_key.attrib['baseTableName']
            fk_column = add_foreign_key.attrib['baseColumnNames']
            pk_table = add_foreign_key.attrib['referencedTableName']
            pk_column = add_foreign_key.attrib['referencedColumnNames']
            relationships.append((fk_table, fk_column, pk_table, pk_column))

    return tables, relationships


def create_drawio_xml(tables, relationships):
    """Создание ER-модели в формате Draw.io XML"""
    mxfile = ET.Element("mxfile")
    diagram = ET.SubElement(mxfile, "diagram", {"name": "ER Model"})
    mxGraphModel = ET.SubElement(diagram, "mxGraphModel")
    root = ET.SubElement(mxGraphModel, "root")

    # Добавляем базовые элементы
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

    table_positions = {}
    x, y = 50, 50

    # Добавляем таблицы
    for table_name, columns in tables.items():
        table_id = generate_id()
        table_positions[table_name] = (table_id, x, y)

        # Создаем элемент таблицы
        table_node = ET.SubElement(root, "mxCell", {
            "id": table_id,
            "value": table_name,
            "style": "shape=rectangle;whiteSpace=wrap;html=1;",
            "vertex": "1",
            "parent": "1"
        })
        geometry = ET.SubElement(table_node, "mxGeometry", {
            "x": str(x), "y": str(y), "width": "120", "height": str(30 + len(columns) * 20)
        })
        geometry.set("as", "geometry")

        # Добавляем колонки в виде текста
        column_text = "\n".join([f"{name}: {col_type}" for name, col_type in columns])
        table_node.attrib["value"] += f"<br>{column_text}"

        # Смещаем позицию для следующей таблицы
        x += 200
        if x > 800:
            x = 50
            y += 200

    # Добавляем связи
    for fk_table, fk_column, pk_table, pk_column in relationships:
        source_id, _, _ = table_positions[fk_table]
        target_id, _, _ = table_positions[pk_table]

        # Создаем линию
        relationship_id = generate_id()
        ET.SubElement(root, "mxCell", {
            "id": relationship_id,
            "edge": "1",
            "source": source_id,
            "target": target_id,
            "style": "edgeStyle=orthogonalEdgeStyle;"
        })

    return mxfile


if __name__ == "__main__":
    liquibase_file = "liquibase.xml"  # Путь к файлу Liquibase
    output_file = "er_model.drawio.xml"  # Имя выходного файла

    # Парсим Liquibase
    tables, relationships = parse_liquibase(liquibase_file)

    # Создаем XML для Draw.io
    drawio_xml = create_drawio_xml(tables, relationships)

    # Сохраняем XML в файл
    tree = ET.ElementTree(drawio_xml)
    with open(output_file, "wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)

    print(f"ER-модель сохранена в файл {output_file}")
