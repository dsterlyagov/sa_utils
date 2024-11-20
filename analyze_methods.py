import os
import re
from collections import defaultdict
from pathlib import Path


class MethodCallAnalyzer:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.method_calls = defaultdict(list)  # Граф вызовов: метод -> [методы]
        self.methods = set()  # Все обнаруженные методы

    def scan_files(self):
        """Сканирует все Java-файлы в заданной директории."""
        java_files = []
        for dirpath, _, filenames in os.walk(self.root_dir):
            for filename in filenames:
                if filename.endswith(".java"):
                    java_files.append(Path(dirpath) / filename)
        return java_files

    def parse_methods_and_calls(self, file_path):
        """Парсит методы и вызовы методов в Java-файле."""
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Регулярное выражение для поиска методов
        method_pattern = re.compile(r"(public|private|protected)?\s+[\w<>]+\s+(\w+)\s*\(.*?\)\s*\{")
        # Регулярное выражение для поиска вызовов методов (включая цепочки методов)
        call_pattern = re.compile(r"(\w+)\.(\w+)\s*\(")

        # Методы, определенные в этом файле
        methods_in_file = set()
        for match in method_pattern.finditer(content):
            method_name = match.group(2)
            methods_in_file.add(method_name)
            self.methods.add(method_name)

        for method in methods_in_file:
            # Найти вызовы внутри каждого метода
            method_body_pattern = re.compile(rf"{method}\s*\(.*?\)\s*\{{(.*?)\}}", re.S)
            method_body_match = method_body_pattern.search(content)
            if method_body_match:
                method_body = method_body_match.group(1)
                self.analyze_body(method, method_body, call_pattern)

    def analyze_body(self, current_method, body, call_pattern):
        """Анализирует тело метода, разделяя блоки if."""
        segments = self.split_into_segments(body)
        for segment in segments:
            self.analyze_segment(current_method, segment, call_pattern)

    def split_into_segments(self, body):
        """Разделяет тело метода на сегменты (блоки if, else, и остальные части)."""
        segments = []
        # Ищем if/else блоки
        if_else_pattern = re.compile(r"(if\s*\(.*?\)\s*\{.*?\}|else\s*\{.*?\})", re.S)
        last_end = 0
        for match in if_else_pattern.finditer(body):
            # Добавить всё до if как сегмент
            if match.start() > last_end:
                segments.append(body[last_end:match.start()].strip())
            # Добавить сам if/else как сегмент
            segments.append(match.group(0).strip())
            last_end = match.end()
        # Добавить остаток после последнего if/else
        if last_end < len(body):
            segments.append(body[last_end:].strip())
        return [seg for seg in segments if seg]

    def analyze_segment(self, current_method, segment, call_pattern):
        """Анализирует отдельный сегмент кода."""
        # Найти вызовы методов в сегменте
        for call_match in call_pattern.finditer(segment):
            object_name, called_method = call_match.groups()
            full_method_name = f"{object_name}.{called_method}"
            self.method_calls[current_method].append(full_method_name)

        # Найти вложенные блоки
        nested_blocks = re.findall(r"\{(.*?)\}", segment, re.S)
        for block in nested_blocks:
            self.analyze_body(current_method, block, call_pattern)

    def analyze(self):
        """Сканирует файлы и строит граф вызовов методов."""
        java_files = self.scan_files()
        for java_file in java_files:
            self.parse_methods_and_calls(java_file)

    def get_call_sequence(self, start_method):
        """Возвращает последовательность вызовов для заданного метода."""
        visited = set()
        sequence = []

        def dfs(method):
            if method not in visited:
                visited.add(method)
                sequence.append(method)
                for called_method in self.method_calls.get(method, []):
                    dfs(called_method)

        dfs(start_method)
        return sequence


def main():
    root_dir = input("Введите путь к корневой папке (например, 'src'): ").strip()
    analyzer = MethodCallAnalyzer(root_dir)
    print("Анализируем файлы...")
    analyzer.analyze()

    print("\nОбнаруженные методы:")
    for method in sorted(analyzer.methods):
        print(f"- {method}")

    start_method = input("\nВведите имя метода для анализа вызовов: ").strip()
    if start_method not in analyzer.methods:
        print(f"Метод '{start_method}' не найден.")
        return

    call_sequence = analyzer.get_call_sequence(start_method)
    print("\nПоследовательность вызовов:")
    for step in call_sequence:
        print(f"- {step}")


if __name__ == "__main__":
    main()
