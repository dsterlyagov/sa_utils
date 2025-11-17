@echo off
setlocal ENABLEDELAYEDEXPANSION

chcp 65001 >nul

REM ==== SETTINGS ====
set "TASK_NAME=WidgetMetaToConfluence"

REM dir of this bat
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM TODO: set correct python path
REM Example for venv:
REM set "PYTHON_EXE=%SCRIPT_DIR%..\venv\Scripts\python.exe"
REM Example for system python:
set "PYTHON_EXE=C:\Python312\python.exe"

set "LOG=%SCRIPT_DIR%confluence.log"

REM ==== INSTALL MODE ====
if /I "%1"=="/install" (
    echo Installing task "%TASK_NAME%" as SYSTEM...
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
        echo [OK] Task "%TASK_NAME%" created.
    )

    pause
    exit /b
)

REM ==== RUN PYTHON SCRIPT ====
echo [%date% %time%] START >> "%LOG%"
"%PYTHON_EXE%" "%SCRIPT_DIR%confluence_save_table_with_presence.py" >> "%LOG%" 2>&1
set "ERR=%ERRORLEVEL%"
echo [%date% %time%] END (exitcode=%ERR%) >> "%LOG%"

endlocal & exit /b %ERR%
