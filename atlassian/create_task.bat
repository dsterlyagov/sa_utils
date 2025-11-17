@echo off
setlocal ENABLEDELAYEDEXPANSION

REM ===== Имя задачи в Планировщике =====
set "TASK_NAME=WidgetMetaToConfluence"

REM ===== Директория, где лежит этот .bat (и run_confluence.bat) =====
set "TASK_DIR=%~dp0"

REM Убираем возможный завершающий \
if "%TASK_DIR:~-1%"=="\" set "TASK_DIR=%TASK_DIR:~0,-1%"

REM Полный путь к run_confluence.bat
set "RUN_BAT=%TASK_DIR%\run_confluence.bat"

echo Creating SYSTEM task "%TASK_NAME%"...
echo TASK_DIR = %TASK_DIR%
echo RUN_BAT  = %RUN_BAT%
echo.

REM === Создаём задачу под SYSTEM ===
schtasks /create ^
 /tn "%TASK_NAME%" ^
 /tr "%RUN_BAT%" ^
 /sc minute ^
 /mo 5 ^
 /ru "SYSTEM" ^
 /rl HIGHEST ^
 /f

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to create task "%TASK_NAME%".
) else (
    echo.
    echo [OK] Task "%TASK_NAME%" created.
    echo It will run every 5 minutes as SYSTEM.
)

echo.
pause
endlocal
