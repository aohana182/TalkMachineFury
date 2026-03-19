@echo off
setlocal

echo === Talk Machine Fury — Setup ===
echo.

:: Check Python 3.10+
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from https://python.org ^(3.10+^)
    echo         During install: check "Add Python to PATH"
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
for /f "tokens=1,2 delims=." %%a in ("%PY_VER%") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)
if %PY_MAJOR% LSS 3 (
    echo [ERROR] Python 3.10+ required, found %PY_VER%
    exit /b 1
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 (
    echo [ERROR] Python 3.10+ required, found %PY_VER%
    exit /b 1
)
echo [OK] Python %PY_VER%

:: Create virtualenv
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)
echo [OK] Virtual environment

:: Activate and install
call .venv\Scripts\activate.bat

echo Installing Python dependencies...
pip install --upgrade pip --quiet
pip install -r server\requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed
    exit /b 1
)
echo [OK] Python dependencies installed

:: Pre-download models (runs at server startup anyway, but nice to do upfront)
echo Pre-downloading models ^(~400MB, one-time^)...
python -c "import onnx_asr; onnx_asr.load_vad('silero'); print('[OK] Silero VAD')"
python -c "import onnx_asr; onnx_asr.load_model('gigaam-v3-e2e-ctc'); print('[OK] GigaAM v3')"
python -c "from faster_whisper import WhisperModel; WhisperModel('Systran/faster-distil-whisper-small.en', compute_type='int8'); print('[OK] distil-whisper')"

:: Create transcript folder
if not exist "C:\Transcripts" (
    mkdir "C:\Transcripts"
    echo [OK] Created C:\Transcripts
) else (
    echo [OK] C:\Transcripts exists
)

:: Register native messaging host (so extension can auto-start the server)
echo Registering native messaging host...
set HOST_BAT=%~dp0server\host.bat
set MANIFEST_SRC=%~dp0server\host_manifest.json
set MANIFEST_DST=%~dp0server\host_manifest_registered.json

:: Write manifest with actual host.bat path (replace backslashes with double backslashes for JSON)
powershell -Command "(Get-Content '%MANIFEST_SRC%') -replace 'PLACEHOLDER_REPLACED_BY_INSTALL_BAT', ('%HOST_BAT%' -replace '\\\\', '\\\\' -replace '\\', '\\\\') | Set-Content '%MANIFEST_DST%'"

:: Register in Windows registry for Brave and Chrome
reg add "HKCU\Software\Google\Chrome\NativeMessagingHosts\com.talkmachinefury.host" /ve /t REG_SZ /d "%MANIFEST_DST:\=\\%" /f >nul
reg add "HKCU\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\com.talkmachinefury.host" /ve /t REG_SZ /d "%MANIFEST_DST:\=\\%" /f >nul
echo [OK] Native messaging host registered

echo.
echo === Setup complete ===
echo.
echo Load the extension in Brave/Chrome:
echo   brave://extensions -^> Developer mode -^> Load unpacked -^> select extension\
echo.
echo The server starts automatically when you click Start in the extension.
echo.
endlocal
