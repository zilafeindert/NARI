@echo off
setlocal
cd /d "%~dp0"
title NARI

if not exist ".venv\Scripts\python.exe" (
  echo NARI no esta instalada. Ejecuta NARI_INSTALAR.bat.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"

rem NARI conoce su repositorio oficial. Comprueba y aplica actualizaciones antes de iniciar.
set NARI_AUTO_UPDATE=1
python nari_update_cli.py --auto
if errorlevel 1 (
  echo.
  echo La comprobacion de actualizaciones fallo; continuando con NARI.
)

python main.py
if errorlevel 1 (
  echo.
  echo NARI se detuvo con un error.
  pause
)
