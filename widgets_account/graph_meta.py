import sys
import json
from pathlib import Path

import yaml
from graphviz import Digraph


def load_yaml(path: Path):
    """
    Загружаем YAML и возвращаем список объектов.
    Поддерживаются варианты:
    - верхний уровень — список
    - верхний уровень — словарь с ключом digitalArchitecture (как в struct.yaml)
    - верхний уровень — словарь с ключом items
    """
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # 1) если сразу список
    if isinstance(data, list):
        return data

    # 2) если словарь – пробуем взять известные ключи
    if isinstance(data, dict):
        if "digitalArchitecture" in data and isinstance(data["digitalArchitecture"], list):
            return data["digitalArchitecture"]
        if "items" in data and isinstance(data["items"], list):
            return data["items"]

    raise ValueError(
        "Ожидался список объектов в YAML (list) или словарь с ключом 'digitalArchitecture' / 'items'"
    )


def build_graph_struct(items):
    """
    Формирует структуру для JSON-графа и для визуализации.

    Включаем только:
    - kind == "Module"  (продуктовый агент)
    - kind == "SubSystem" (доменный агент)

    Связи строим по parentYamlId -> yamlId,
    но только если оба элемента тоже Module/SubSystem.
    """
    # отфильтруем только нужные виды
    filtered = [i for i in items if i.get("kind") in ("Module", "SubSystem")]

    # индекс по yamlId среди отфильтрованных
    by_id = {str(item["yamlId"]): item for item in filtered}

    nodes = []
    edges = []

    for item in filtered:
        yaml_id = str(item.get("yamlId"))
        parent_id = item.get("parentYamlId")
        if parent_id is not None:
            parent_id = str(parent_id)

        kind = item.get("kind")
        name = item.get("name")
        id_app = item.get("idApp")

        # тип агента для удобства
        if kind == "Module":
            agent_type = "product"   # продуктовый агент
        else:  # SubSystem
            agent_type = "domain"    # доменный агент

        nodes.append(
            {
                "id": yaml_id,
                "kind": kind,
                "agentType": agent_type,
                "name": name,
                "idApp": id_app,
            }
        )

        # ребро по иерархии, только если родитель тоже в нашей выборке
        if parent_id is not None and parent_id in by_id:
            edges.append(
                {
                    "source": parent_id,
                    "target": yaml_id,
                    "relation": "hierarchy",
                }
            )

    graph_json = {"nodes": nodes, "edges": edges}
    return graph_json


def save_json(graph_json, path: Path):
    with path.open("w", encoding="utf-8") as f:
        json.dump(graph_json, f, ensure_ascii=False, indent=2)


def render_graph(graph_json, path: Path):
    """
    Визуализация с помощью Graphviz (формат PNG).

    - Module   — прямоугольник (box) — продуктовый агент
    - SubSystem — эллипс (ellipse) — доменный агент
    """
    dot = Digraph(comment="Module-SubSystem graph", format="png")
    # Можно поменять на "TB" (top-bottom) если нужна вертикальная иерархия
    dot.attr(rankdir="LR")

    # добавляем вершины
    for node in graph_json["nodes"]:
        node_id = node["id"]
        name = node.get("name") or ""
        id_app = node.get("idApp") or ""
        kind = node.get("kind") or ""

        label = f"{name}\\n({kind}, idApp={id_app})"

        if kind == "Module":
            shape = "box"
        elif kind == "SubSystem":
            shape = "ellipse"
        else:
            shape = "plaintext"

        dot.node(node_id, label=label, shape=shape)

    # добавляем рёбра
    for edge in graph_json["edges"]:
        dot.edge(edge["source"], edge["target"])

    # сохраняем (Graphviz сам добавит .png)
    out_path = path.with_suffix("")  # убираем .png, graphviz добавит его сам
    dot.render(str(out_path), cleanup=True)


def main():
    if len(sys.argv) != 4:
        print(
            "Использование:\n"
            "  python graph_agents.py input.yaml output.json graph.png"
        )
        sys.exit(1)

    input_yaml = Path(sys.argv[1])
    output_json = Path(sys.argv[2])
    output_graph = Path(sys.argv[3])

    items = load_yaml(input_yaml)
    graph_json = build_graph_struct(items)
    save_json(graph_json, output_json)
    render_graph(graph_json, output_graph)

    print(f"JSON-граф сохранён в: {output_json}")
    print(f"Картинка графа сохранена в: {output_graph}")


if __name__ == "__main__":
    main()
