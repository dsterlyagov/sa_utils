#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

# ---------- утилиты ----------

def _read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _is_windows() -> bool:
    return os.name == "nt"

def _bin_candidates_tsx(project_root: Path) -> List[Path]:
    """
    На Windows сначала ищем .../.bin/tsx.cmd (правильная обертка), на *nix — .../.bin/tsx
    Системный 'tsx' намеренно НЕ используем (чтобы не упереться в отсутствующий npx/глобал).
    """
    bins: List[Path] = []
    bin_dir = project_root / "node_modules" / ".bin"
    if _is_windows():
        p_cmd = (bin_dir / "tsx.cmd").resolve()
        if p_cmd.exists():
            bins.append(p_cmd)
        # Не используем бинарь без .cmd — вызовет WinError 193
    else:
        p = (bin_dir / "tsx").resolve()
        if p.exists():
            bins.append(p)
    return bins

def _pick_project_root(script_path: Path) -> Path:
    # логично: если файл лежит в scripts/, на уровень выше — корень
    return script_path.parents[1] if script_path.parent.name.lower() in {"scripts", "script"} else script_path.parent

# ---------- запуск TS ----------

def run_ts_with_tsx(script: Path, project_root: Path, timeout_sec: int) -> None:
    tsx_bins = _bin_candidates_tsx(project_root)
    if not tsx_bins:
        raise RuntimeError(
            "Не найден локальный tsx. Установите его в проект:\n"
            "  npm i -D tsx\n"
            "или используйте рабочую копию проекта, где уже есть node_modules/.bin/tsx(.cmd)."
        )

    cmd = [str(tsx_bins[0]), str(script)]
    print("> Запуск:", " ".join(cmd))

    # Важно: Windows-stdout может быть CP1251, но tsx печатает UTF-8 → явно декодируем UTF-8
    proc = subprocess.Popen(
        cmd,
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",  # не падать на «умных» кавычках
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

# ---------- ожидание файла ----------

def wait_for_file(path: Path, timeout_sec: int) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.2)
    raise TimeoutError(f"Файл не появился за {timeout_sec} сек: {path}")

# ---------- main ----------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Запуск build-meta-from-zod.ts и ожидание JSON без записи в Confluence"
    )
    ap.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    ap.add_argument(
        "--outdir",
        required=True,
        help="Папка для результата (ОТНОСИТЕЛЬНО ТЕКУЩЕЙ РАБОЧЕЙ ДИРЕКТОРИИ, если путь относительный)"
    )
    ap.add_argument("--outfile", default="widget-meta.json", help="Имя файла результата (по умолчанию widget-meta.json)")
    ap.add_argument("--timeout", type=int, default=300, help="Таймаут выполнения TS-скрипта, сек (default 300)")
    ap.add_argument("--wait", type=int, default=120, help="Таймаут ожидания файла, сек (default 120)")
    args = ap.parse_args()

    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Не найден скрипт: {script}")

    # outdir: интерпретируем относительно ТЕКУЩЕЙ РАБОЧЕЙ ДИРЕКТОРИИ
    outdir = Path(args.outdir)
    if not outdir.is_absolute():
        outdir = Path.cwd() / outdir
    outdir = outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    project_root = _pick_project_root(script)

    # Запуск строго через локальный tsx(.cmd)
    run_ts_with_tsx(script, project_root, timeout_sec=args.timeout)

    # Ожидание файла
    print("> Ожидание файла:", outfile)
    wait_for_file(outfile, timeout_sec=args.wait)
    print(f"> Найден файл: {outfile} ({outfile.stat().st_size} bytes)")

    # Быстрая проверка JSON
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
