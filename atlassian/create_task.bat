@echo off
setlocal ENABLEDELAYEDEXPANSION

chcp 65001 >nul

REM ==== SETTINGS ====
set "TASK_NAME=WidgetMetaToConfluence"

REM dir of this bat
set "SCRIPT_DIR=%~dp0"

REM remove trailing backslash if any
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

REM TODO: set correct python path
REM Example for venv:
REM set "PYTHON_EXE=%SCRIPT_DIR%\..\venv\Scripts\python.exe"
REM Example for system python:
set "PYTHON_EXE=C:\Python312\python.exe"

set "LOG=%SCRIPT_DIR%\confluence.log"

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
echo [%%date%% %%time%%] START >> "%LOG%"
echo [INFO] SCRIPT_DIR="%SCRIPT_DIR%" >> "%LOG%"
echo [INFO] PYTHON_EXE="%PYTHON_EXE%" >> "%LOG%"
echo [INFO] LOG="%LOG%" >> "%LOG%"

REM check SCRIPT_DIR exists
if not exist "%SCRIPT_DIR%" (
    echo [ERROR] SCRIPT_DIR does not exist: "%SCRIPT_DIR%" >> "%LOG%"
    echo [ERROR] SCRIPT_DIR does not exist: "%SCRIPT_DIR%"
    endlocal & exit /b 3
)

cd /d "%SCRIPT_DIR%" || (
    echo [ERROR] cd to "%SCRIPT_DIR%" failed >> "%LOG%"
    endlocal & exit /b 3
)

REM check python exists
if not exist "%PYTHON_EXE%" (
    echo [ERROR] PYTHON_EXE not found: "%PYTHON_EXE%" >> "%LOG%"
    echo [ERROR] PYTHON_EXE not found: "%PYTHON_EXE%"
    endlocal & exit /b 3
)

REM check main script exists
if not exist "%SCRIPT_DIR%\confluence_save_table_with_presence.py" (
    echo [ERROR] main script not found: "%SCRIPT_DIR%\confluence_save_table_with_presence.py" >> "%LOG%"
    echo [ERROR] main script not found: "%SCRIPT_DIR%\confluence_save_table_with_presence.py"
    endlocal & exit /b 3
)

"%PYTHON_EXE%" "%SCRIPT_DIR%\confluence_save_table_with_presence.py" >> "%LOG%" 2>&1
set "ERR=%ERRORLEVEL%"
echo [%%date%% %%time%%] END (exitcode=%ERR%) >> "%LOG%"

endlocal & exit /b %ERR%
