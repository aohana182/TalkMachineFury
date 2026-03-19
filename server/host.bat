@echo off
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe server\host.py
) else (
    python server\host.py
)
