@echo off
setlocal
cd /d "C:\flask_project" || (echo Folder not found & pause & exit /b 1)

if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
) else (
  echo Creating virtual environment...
  py -m venv .venv || (echo Failed to create venv & pause & exit /b 1)
  call ".venv\Scripts\activate.bat"
  py -m pip install --upgrade pip
  pip install flask pdfkit
)

REM Start server in a new window that stays open
start "Flask Server" cmd /k "py app.py"

REM Wait until it’s listening, then open browser (up to 30s)
for /l %%i in (1,1,30) do (
  >nul 2>&1 powershell -command "(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',5000)"
  if not errorlevel 1 goto :open
  timeout /t 1 >nul
)
echo Server didn’t start within 30 seconds.
pause
exit /b 1

:open
start "" "http://127.0.0.1:5000/"
exit /b 0