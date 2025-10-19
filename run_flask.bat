@echo off
cd /d "C:\flask_project"
python import_products.py
python check_db_for_dups.py
start "" cmd /k "python app.py"
timeout /t 7 >nul

REM Open Edge in App mode (no address bar, tabs, bookmarks, but taskbar visible!)
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --app="http://127.0.0.1:5000"

pause
