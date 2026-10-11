@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title NARI

if not exist ".venv\Scripts\python.exe" (
  echo NARI no esta instalada o falta su entorno virtual.
  echo Ejecuta NARI_INSTALAR.bat para instalarla de nuevo.
  pause
  exit /b 1
)

rem Detectar entornos virtuales que existen pero quedaron apuntando a un Python eliminado.
".venv\Scripts\python.exe" -c "import sys, requests" >nul 2>&1
if errorlevel 1 (
  echo El Python de NARI no responde. Intentando reparar el entorno sin borrar datos...
  if not exist "NARI_REPARAR_PYTHON.bat" (
    echo Falta NARI_REPARAR_PYTHON.bat. Descargalo de la version mas reciente de NARI.
    pause
    exit /b 1
  )
  call "NARI_REPARAR_PYTHON.bat"
  if errorlevel 1 (
    echo No se pudo reparar Python automaticamente.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -c "import sys, requests" >nul 2>&1
if errorlevel 1 (
  echo El entorno de Python sigue sin funcionar. No se iniciara NARI.
  pause
  exit /b 1
)

rem Usa siempre el interprete del entorno virtual; no depender del PATH global de Windows.
set "NARI_AUTO_UPDATE=1"
".venv\Scripts\python.exe" nari_update_cli.py --auto

".venv\Scripts\python.exe" main.py
if errorlevel 1 (
  echo.
  echo NARI se detuvo con un error. Revisa el mensaje anterior.
  pause
)
