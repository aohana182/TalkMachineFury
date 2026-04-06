@echo off
:: Kill any existing server on port 8765
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765 "') do (
    taskkill /F /PID %%p >nul 2>&1
)

:: Start the server — use venv if installed, fallback to system Python
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -m uvicorn server.main:app --port 8765
) else (
    python -m uvicorn server.main:app --port 8765
)
