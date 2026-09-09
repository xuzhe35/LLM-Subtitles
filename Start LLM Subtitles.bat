@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment not found: %CD%\.venv
    echo Create it and install dependencies before starting the application.
    echo.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python main.py
set "exit_code=%ERRORLEVEL%"

if not "%exit_code%"=="0" (
    echo.
    echo LLM Subtitles exited with error code %exit_code%.
    pause
)

endlocal & exit /b %exit_code%
