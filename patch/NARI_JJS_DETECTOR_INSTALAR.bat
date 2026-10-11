@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title NARI - Instalar detector visual JJS

set "NARI_PID=%~1"
set "AUTO_RESTART=%~2"

rem NARI loads cv2.pyd in its own process. Do not upgrade OpenCV until that process exits.
if defined NARI_PID (
  echo Esperando a que NARI cierre para liberar cv2.pyd...
  :wait_nari
  tasklist /FI "PID eq %NARI_PID%" 2>NUL | find "%NARI_PID%" >NUL
  if not errorlevel 1 (
    timeout /t 1 /nobreak >NUL
    goto wait_nari
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo No encuentro .venv\Scripts\python.exe.
  echo Abre la carpeta de NARI y repara o instala su entorno Python.
  goto failed
)

echo.
echo Instalando el detector local YOLO en el entorno de NARI...
echo Esta instalacion puede descargar dependencias grandes de PyTorch.
".venv\Scripts\python.exe" -m pip install ultralytics
if errorlevel 1 (
  echo.
  echo No se pudo instalar Ultralytics.
  goto failed
)

echo.
echo Descargando/preparando el modelo compacto YOLOv8n...
".venv\Scripts\python.exe" -c "from ultralytics import YOLO; m=YOLO('yolov8n.pt'); print('Modelo listo:', type(m).__name__)"
if errorlevel 1 (
  echo.
  echo La libreria se instalo, pero el modelo no pudo cargarse.
  echo Comprueba tu conexion y vuelve a ejecutar este archivo.
  goto failed
)

echo.
echo Detector instalado correctamente.
echo NARI usara YOLO para proponer avatares y el modelo visual para interpretar el combate.
goto finish

:failed
echo.
echo La instalacion fallo. NARI puede seguir usando la vision VLM como respaldo.

:finish
if /I "%AUTO_RESTART%"=="--restart" (
  echo.
  echo Volviendo a iniciar NARI...
  start "" ".venv\Scripts\python.exe" "main.py"
  timeout /t 2 /nobreak >NUL
  exit /b
)
echo Cierra NARI antes de volver a ejecutar este instalador. Si NARI estaba abierto, espera a que se cierre por completo.
pause
