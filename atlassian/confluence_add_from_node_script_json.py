#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Запуск TypeScript-скрипта build-meta-from-zod.ts + проверка результата.

Что делает:
  1) Ищет build-meta-from-zod.ts рядом или по пути из NODE_SCRIPT.
  2) Пытается запустить через один из раннеров (по порядку):
        - npx -y tsx <script>
        - npx -y ts-node --transpile-only <script>
        - node --loader ts-node/esm <script>
     (первый удачный — победил)
  3) Создаёт папку output при необходимости.
  4) Ждёт появления файла результата (по умолчанию output/widget-meta.json).
  5) Валидирует структуру результата против эталона (из widget-meta.json, если он есть рядом,
     либо валидирует базовые поля).
Переменные окружения:
  NODE_SCRIPT      — путь к build-meta-from-zod.ts (по умолчанию ./build-meta-from-zod.ts)
  OUTPUT_DIR       — путь к папке для результатов (по умолчанию ./output)
  OUTPUT_FILENAME  — имя файла результата (по умолчанию widget-meta.json)
  EXEC_TIMEOUT_SEC — общий таймаут на выполнение (по умолчанию 300 сек)
"""

import json
import os
import sys
import time
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ---- настройки по умолчанию ----
NODE_SCRIPT = os.getenv("NODE_SCRIPT", "./build-meta-from-zod.ts")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")
OUTPUT_FILENAME = os.getenv("OUTPUT_FILENAME", "widget-meta.json")
EXEC_TIMEOUT_SEC = int(os.getenv("EXEC_TIMEOUT_SEC", "300"))

RUNNERS: List[List[str]] = [
    ["npx", "-y", "tsx"],                          # лучший вариант для ESM/TS без настройки
    ["npx", "-y", "ts-node", "--transpile-only"],  # быстро и без типчека
    ["node", "--loader", "ts-node/esm"],           # если ts-node/esm доступен как лоадер
]

def _which(bin_name: str) -> bool:
    return shutil.which(bin_name) is not None

def pick_runner(script: str) -> List[str]:
    """
    Возвращает команду запуска (список argv) для доступного раннера.
    Бросает исключение, если ни один не найден.
    """
    errors: List[str] = []
    for runner in RUNNERS:
        # проверяем наличие первого бинаря в команде
        if not _which(runner[0]):
            errors.append(f"skip {' '.join(runner)}: '{runner[0]}' не найден в PATH")
            continue
        # "сухой" прогон --version (кроме node --loader …)
        try:
            if runner[0] == "node":
                # node почти всегда есть, пробуем сразу с лоадером — реальную ошибку поймаем на запуске
                return runner + [script]
            else:
                subprocess.run([runner[0], "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                return runner + [script]
        except Exception as e:
            errors.append(f"skip {' '.join(runner)}: {e}")
            continue

    raise RuntimeError(
        "Не найден подходящий раннер TypeScript (tsx/ts-node). "
        + "Поставьте любой из них: `npm i -g tsx` или используйте `npx -y tsx`.\n"
        + "\n".join(errors)
    )

def run_ts(script_path: Path) -> None:
    cmd = pick_runner(str(script_path))
    print(f"▶️  Запуск: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    start = time.time()
    # Стримим лог, пока не кончится таймаут
    line: str
    try:
        while True:
            if proc.poll() is not None:
                break
            if time.time() - start > EXEC_TIMEOUT_SEC:
                proc.kill()
                raise TimeoutError(f"Превышен таймаут {EXEC_TIMEOUT_SEC} сек при выполнении TypeScript-скрипта")
            line = proc.stdout.readline()
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                time.sleep(0.05)
        # дочитываем хвост
        tail = proc.stdout.read()
        if tail:
            sys.stdout.write(tail)
        if proc.returncode != 0:
            raise RuntimeError(f"TS-скрипт завершился с кодом {proc.returncode}")
    finally:
        try:
            proc.stdout.close()  # type: ignore
        except Exception:
            pass

def wait_for_file(path: Path, timeout_sec: int = 60) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.2)
    raise TimeoutError(f"Файл результата не появился в течение {timeout_sec} сек: {path}")

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def validate_against_sample(result: Any, sample: Any) -> List[str]:
    """
    Очень лёгкая валидация: сверяем типы верхнего уровня и базовые поля.
    Если sample — это реальный пример (из widget-meta.json), пробегаемся по ключам/типам.
    """
    errors: List[str] = []
    if not isinstance(result, dict):
        errors.append(f"Ожидался объект JSON, получили: {type(result).__name__}")
        return errors
    if isinstance(sample, dict):
        for k, v in sample.items():
            if k not in result:
                errors.append(f"Нет обязательного поля: {k}")
                continue
            if isinstance(v, dict) and not isinstance(result[k], dict):
                errors.append(f"Поле {k}: ожидался объект, получили {type(result[k]).__name__}")
            if isinstance(v, list) and not isinstance(result[k], list):
                errors.append(f"Поле {k}: ожидался массив, получили {type(result[k]).__name__}")
    # Бонус: базовые поля, которые обычно ожидаем в «мета»-файле
    for must in ("toolsMeta",):
        if must not in result:
            errors.append(f"Нет поля '{must}'")
    return errors

def main() -> None:
    # 0) Нормализуем пути
    script_path = Path(NODE_SCRIPT).resolve()
    output_dir = Path(OUTPUT_DIR).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / OUTPUT_FILENAME

    if not script_path.exists():
        raise FileNotFoundError(f"Не найден TypeScript-скрипт: {script_path}\n"
                                f"Подсказка: ошибка MODULE_NOT_FOUND при запуске node .ts без раннера — ожидаема.")

    # 1) Запускаем TS
    run_ts(script_path)

    # 2) Ждём результата
    wait_for_file(output_file, timeout_sec=120)
    print(f"✅ Найден результат: {output_file} ({output_file.stat().st_size} байт)")

    # 3) Валидируем структуру
    result_json = load_json(output_file)

    # пытаемся найти эталон рядом с Python-скриптом или в текущей директории
    possible_sample = [
        Path.cwd() / "widget-meta.json",
        Path(__file__).resolve().parent / "widget-meta.json",
    ]
    sample = None
    for p in possible_sample:
        if p.exists():
            sample = load_json(p)
            break

    errors = validate_against_sample(result_json, sample or {})
    if errors:
        print("⚠️  Найдены несоответствия структуре образца:")
        for e in errors:
            print("   - " + e)
        sys.exit(3)
    else:
        print("🟢 Структура результата выглядит корректной.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
