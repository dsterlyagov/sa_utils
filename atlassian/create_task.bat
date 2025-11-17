@echo off
setlocal ENABLEDELAYEDEXPANSION

chcp 65001 >nul

REM ===========================================================
REM ========== НАСТРОЙКИ ======================================
REM ===========================================================

REM Имя задачи в Планировщике
set "TASK_NAME=WidgetMetaToConfluence"

REM Директория, где лежит этот .bat
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM Путь к python.exe — ОБЯЗАТЕЛЬНО УКАЖИ СВОЙ
REM ВАРИАНТ 1: виртуальное окружение
REM set "PYTHON_EXE=%SCRIPT_DIR%..\venv\Scripts\python.exe"

REM ВАРИАНТ 2: системный Python
set "PYTHON_EXE=C:\Python312\python.exe"

REM Лог-файл
set "LOG=%SCRIPT_DIR%confluence.log"


REM ===========================================================
REM ========== 1. РЕЖИМ ИНСТАЛЛЯЦИИ ЗАДАЧИ ====================
REM ===========================================================

if /i "%1"=="/install" (

    echo Installing task "%TASK_NAME%" to Task Scheduler...
    echo This file will be executed every 5 minutes as SYSTEM.
    echo.

    REM Создаём задачу, которая запускает ЭТОТ ЖЕ .bat
    schtasks /create ^
     /tn "%TASK_NAME%" ^
     /tr "\"%~f0\"" ^
     /sc minute ^
     /mo 5 ^
     /ru SYSTEM ^
     /rl HIGHEST ^
     /f

    if errorlevel 1 (
        echo [ERROR] Task creation failed.
    ) else (
        echo [OK] Task "%TASK_NAME%" installed successfully.
    )

    echo.
    pause
    exit /b
)


REM ===========================================================
REM ========== 2. РЕЖИМ ЗАПУСКА САМОГО PYTHON =================
REM ===========================================================

echo [%date% %time%] START >> "%LOG%"

"%PYTHON_EXE%" "%SCRIPT_DIR%confluence_save_table_with_presence.py" >> "%LOG%" 2>&1
set "ERR=%ERRORLEVEL%"

echo [%date% %time%] END (exitcode=%ERR%) >> "%LOG%"

exit /b %ERR%
