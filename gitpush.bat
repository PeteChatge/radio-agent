@echo off
cd /d "%~dp0"
git remote get-url origin >nul 2>&1 || git remote add origin https://github.com/PeteChatge/radio-agent.git
git add .
git diff --cached --quiet && echo Nichts zu committen. && goto PUSH
if "%~1"=="" (
  git commit -m "update %date% %time%"
) else (
  git commit -m "%*"
)
:PUSH
git push -u origin main
pause
