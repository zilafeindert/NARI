@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ejecuta NARI_INSTALAR.bat primero.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
python -c "import json; from pathlib import Path; p=Path('data/settings.json'); p.parent.mkdir(exist_ok=True); s=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}; s['github_repo']='zilafeindert/NARI'; p.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8'); print('Fuente oficial: zilafeindert/NARI')"
echo.
echo El actualizador de NARI queda configurado permanentemente.
pause
