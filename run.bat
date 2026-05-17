@echo off
python pipeline.py
if errorlevel 1 (
    echo Pipeline failed.
    pause
    exit /b 1
)
start events.html

echo Publishing to GitHub Pages...
copy /y "%~dp0events.html" "%~dp0index.html" >nul
git -C "%~dp0" add events.html events.json index.html
git -C "%~dp0" commit -m "Update events" 2>nul
git -C "%~dp0" push
echo Done. Live at https://mim21.github.io/merhav-bari/
