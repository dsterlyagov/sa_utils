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
from typing import List, Optional, Tuple

def read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def detect_module_mode(project_root: Path) -> str:
    """Возвращает 'esm' | 'cjs' | 'unknown' по package.json/tsconfig."""
    pkg = read_json(project_root / "package.json") or {}
    if pkg.get("type") == "module":
        return "esm"
    tsconfig = read_json(project_root / "tsconfig.json") or {}
    comp = tsconfig.get("compilerOptions") or {}
    module = (comp.get("module") or "").lower()
    if module.startswith("es"):
        return "esm"
    if module in {"commonjs", "cjs"}:
        return "cjs"
    return "unknown"

def which(path_or_cmd: str) -> Optional[str]:
    return shutil.which(path_or_cmd)

def candidate_binaries(project_root: Path) -> dict:
    """Ищем локальные бинарники в node_modules/.bin + системные."""
    bin_dir = project_root / "node_modules" / ".bin"
    # Windows добавляет .cmd
    win = os.name == "nt"
    def variant(name: str) -> List[str]:
        paths = []
        if win:
            p = str((bin_dir / f"{name}.cmd").resolve())
            if os.path.exists(p):
                paths.append(p)
        p2 = str((bin_dir / name).resolve())
        if os.path.exists(p2):
            paths.append(p2)
        if which(name):
            paths.append(name)
        return paths
    return {
        "tsx": variant("tsx"),
        "tsnode": variant("ts-node"),
        "node": variant("node") or ["node"],
        "npx": variant("npx"),
    }

def first_available(cmd_lists: List[List[str]]) -> Optional[List[str]]:
    for cmd in cmd_lists:
        exe = cmd[0]
        if which(exe) or os.path.isabs(exe):
            return cmd
    return None

def pick_runners(script: Path, project_root: Path) -> List[List[str]]:
    """
    Возвращает упорядоченный список возможных команд (argv) для запуска TS.
    Учитывает ESM/CJS и наличие локальных бинарников.
    """
    bins = candidate_binaries(project_root)
    mode = detect_module_mode(project_root)

    runners: List[List[str]] = []

    # 1) TSX (локальный → npx → системный)
    for tsx in bins["tsx"]:
        runners.append([tsx, str(script)])
    if bins["npx"]:
        runners.append([bins["npx"][0], "-y", "tsx", str(script)])

    # 2) ts-node ESM приоритетно, если ESM
    if mode in ("esm", "unknown"):
        for tsn in bins["tsnode"]:
            runners.append([tsn, "--esm", "--transpile-only", str(script)])
        runners.append([bins["node"][0], "--loader", "ts-node/esm", str(script)])

    # 3) ts-node CJS — если CJS или как запасной
    if mode in ("cjs", "unknown"):
        for tsn in bins["tsnode"]:
            runners.append([tsn, "--transpile-only", str(script)])
        runners.append([bins["node"][0], "-r", "ts-node/register", str(script)])

    # Удалим дубликаты по строке
    uniq = []
    seen = set()
    for r in runners:
        key = " ".join(r)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq

def run_stream(cmd: List[str], cwd: Path, timeout_sec: int) -> None:
    print("> Запуск:", " ".join(cmd))
    proc = subprocess.Popen(
        cmd, cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, shell=False
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

def wait_for_file(path: Path, timeout_sec: int) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.2)
    raise TimeoutError(f"Файл не появился за {timeout_sec} сек: {path}")

def main() -> None:
    ap = argparse.ArgumentParser(description="Запуск build-meta-from-zod.ts и ожидание widget-meta.json")
    ap.add_argument("--script", required=True, help="Путь к build-meta-from-zod.ts")
    ap.add_argument("--outdir", required=True, help="Папка output")
    ap.add_argument("--outfile", default="widget-meta.json", help="Имя файла результата (по умолчанию widget-meta.json)")
    ap.add_argument("--timeout", type=int, default=300, help="Таймаут выполнения TS-скрипта, сек (по умолчанию 300)")
    ap.add_argument("--wait", type=int, default=120, help="Таймаут ожидания файла, сек (по умолчанию 120)")
    args = ap.parse_args()

    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Не найден скрипт: {script}")

    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / args.outfile

    # предполагаемый корень проекта: на уровень выше папки scripts/
    project_root = script.parents[1] if script.parent.name.lower() in {"scripts", "script"} else script.parent
    cwd = project_root  # важен для относительных импортов в TS

    # построим список кандидатов и попробуем по очереди
    for cmd in pick_runners(script, project_root):
        try:
            run_stream(cmd, cwd=cwd, timeout_sec=args.timeout)
            break
        except Exception as e:
            print(f"! Runner failed: {' '.join(cmd)}")
            print(f"  Reason: {e}")
            continue
    else:
        raise RuntimeError("Не удалось запустить TypeScript-скрипт ни одним раннером (tsx/ts-node/node).")

    print("> Ожидание файла:", outfile)
    wait_for_file(outfile, timeout_sec=args.wait)
    print(f"> Найден файл: {outfile} ({outfile.stat().st_size} bytes)")

    # проверим JSON
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
