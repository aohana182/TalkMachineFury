@echo off
:: Kill any existing server on port 8765
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765 "') do (
    taskkill /F /PID %%p >nul 2>&1
)

:: Start the server
cd /d "%~dp0"
C:\Users\avioh\AppData\Local\Programs\Python\Python312\python.exe -m uvicorn server.main:app --port 8765
