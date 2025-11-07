#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Запускает TS-скрипт (build-meta-from-zod.ts) так, чтобы он стабильно отработал на Windows:
 - пробует npx tsx  (рекомендуется; не требует настройки ESM/CJS)
 - затем npx ts-node --transpile-only
 - затем node -r ts-node/register
 - затем node --loader ts-node/esm  (как крайний случай)

Далее ждёт появления output/widget-meta.json и проверяет JSON.

Пример:
  python build_meta_runner.py ^
    --script .\widget-store\scripts\build-meta-from-zod.ts ^
    --outdir .\widget-store\output ^
    --outfile widget-meta.json
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

# ---- стратегии запуска TS ----
RUNNERS = [
    ["npx", "-y", "tsx"],                           # 1) лучший путь: сам разрулит ESM/CJS
    ["npx", "-y", "ts-node", "--transpile-only"],   # 2) быстро, без типчека
    ["node", "-r", "ts-node/register"],             # 3) preload ts-node как CJS
    ["node", "--loader", "ts-node/esm"],            # 4) ESM-лоадер (может требовать ESM-настройки)
]

def which_or_none(name: str) -> Optional[str]:
    return shutil.which(name)

def pick_runner(script: Path) -> List[str]:
    """
    Возвращает команду запуска (argv) для первого доступного раннера.
    Бросает RuntimeError, если ничего не подошло.
    """
    errors = []
    for candidate in RUNNERS:
        exe = candidate[0]
        if not which_or_none(exe):
            errors.append(f"skip {' '.join(candidate)}: '{exe}' не найден в PATH")
            continue

        # node обычно есть; для npx проверим версию (если можно)
        try:
            if exe != "node":
                subprocess.run([exe, "--version"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=True)
        except Exception as e:
            errors.append(f"skip {' '.join(candidate)}: {e}")
            continue

        # если дошли сюда — runner доступен
        return candidate + [str(script)]

    raise RuntimeError(
        "Не найден ни один подходящий раннер TypeScript (tsx/ts-node/node). "
        "Установите Node.js и npm; затем можно запускать через 'npx -y tsx'.\n"
        + "\n".join(errors)
    )

def run_ts(cmd: List[str], cwd: Path, timeout_sec: int) -> None:
    """
    Запускает TS-скрипт и стримит вывод. Падает, если код возврата != 0 либо таймаут.
    """
    print(f"> Запуск: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=False
    )
    start = time.time()
    try:
        while True:
            if proc.poll() is not None:
                break
            if time.time() - start > timeout_sec:
                proc.kill()
                raise TimeoutError(f"Превышен таймаут {timeout_sec} сек")
            line = proc.stdout.readline()  # type: ignore
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                time.sleep(0.05)
        # дочитываем остаток
        tail = proc.stdout.read() if proc.stdout else ""  # type: ignore
        if tail:
            sys.stdout.write(tail)
        if proc.returncode != 0:
            raise RuntimeError(f"Процесс завершился с кодом {proc.returncode}")
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass

def wait_for_file(path: Path, timeout_sec: int) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.2)
    raise TimeoutError(f"Файл не появился за {timeout_sec} сек: {path}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Запуск build-meta-from-zod.ts и ожидание widget-meta.json")
    parser.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    parser.add_argument("--outdir", required=True, help="Папка output (куда пишет TS)")
    parser.add_argument("--outfile", default="widget-meta.json", help="Имя файла результата (по умолчанию widget-meta.json)")
    parser.add_argument("--timeout", type=int, default=300, help="Таймаут выполнения TS-скрипта, сек (default 300)")
    parser.add_argument("--wait", type=int, default=120, help="Таймаут ожидания файла результата, сек (default 120)")
    args = parser.parse_args()

    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Не найден скрипт: {script}")

    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    # определяем рабочую директорию (корень проекта), чтобы TS видел относительные импорты
    # логично взять корень монорепо / проект: папка выше scripts/
    cwd = script.parent.parent if script.parent.name.lower() in {"scripts", "script"} else script.parent

    # подбираем раннер и запускаем
    cmd = pick_runner(script)
    run_ts(cmd, cwd=cwd, timeout_sec=args.timeout)

    # ждём файл
    print("> Ожидание файла результата:", outfile)
    wait_for_file(outfile, timeout_sec=args.wait)
    size = outfile.stat().st_size
    print(f"> Найден файл: {outfile} ({size} байт)")

    # валидация JSON
    try:
        with outfile.open("r", encoding="utf-8") as f:
            json.load(f)
        print("> JSON корректен.")
    except Exception as e:
        print(f"! Предупреждение: не удалось распарсить JSON: {e}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
