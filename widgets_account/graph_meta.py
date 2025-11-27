import sys
import json
from pathlib import Path

import yaml
from graphviz import Digraph


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # поддержка двух вариантов: просто список или {items: [...]}
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
    else:
        items = data

    if not isinstance(items, list):
        raise ValueError("Ожидался список объектов в YAML")

    return items


def build_graph_struct(items):
    """
    Формирует структуру для JSON-графа и для визуализации.
    Вершины: Module (product agent), SubSystem (domain agent)
    Рёбра: parentYamlId -> yamlId
    """
    by_id = {str(item["yamlId"]): item for item in items}

    nodes = []
    edges = []

    for item in items:
        yaml_id = str(item.get("yamlId"))
        parent_id = item.get("parentYamlId")
        if parent_id is not None:
            parent_id = str(parent_id)

        kind = item.get("kind")
        name = item.get("name")
        id_app = item.get("idApp")

        # тип агента
        if kind == "Module":
            agent_type = "product"   # продуктовый агент
        elif kind == "SubSystem":
            agent_type = "domain"    # доменный агент
        else:
            agent_type = "other"

        nodes.append(
            {
                "id": yaml_id,
                "kind": kind,
                "agentType": agent_type,
                "name": name,
                "idApp": id_app,
            }
        )

        # ребро по иерархии
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
    Module — прямоугольник, SubSystem — эллипс.
    """
    dot = Digraph(comment="Module-SubSystem graph", format="png")
    dot.attr(rankdir="LR")  # можно поменять на TB для вертикальной иерархии

    # добавляем вершины
    for node in graph_json["nodes"]:
        node_id = node["id"]
        label = f'{node.get("name", "")}\\n({node["kind"]}, idApp={node.get("idApp")})'

        if node["kind"] == "Module":
            shape = "box"
        elif node["kind"] == "SubSystem":
            shape = "ellipse"
        else:
            shape = "plaintext"

        dot.node(node_id, label=label, shape=shape)

    # добавляем рёбра
    for edge in graph_json["edges"]:
        dot.edge(edge["source"], edge["target"])

    # сохраняем
    out_path = path.with_suffix("")  # graphviz сам добавит .png
    dot.render(str(out_path), cleanup=True)


def main():
    if len(sys.argv) != 4:
        print(
            "Использование:\n"
            "  python yaml_to_graph.py input.yaml output.json graph.png"
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
