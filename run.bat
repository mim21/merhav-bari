@echo off
python pipeline.py
if errorlevel 1 (
    echo Pipeline failed.
    pause
    exit /b 1
)
start index.html

echo Publishing to GitHub Pages...
git -C "%~dp0" add index.html events.json pipeline.py
git -C "%~dp0" commit -m "Update events" 2>nul
git -C "%~dp0" push
echo Done. Live at https://mim21.github.io/merhav-bari/
