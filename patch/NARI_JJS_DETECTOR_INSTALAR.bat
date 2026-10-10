@echo off
setlocal
cd /d "%~dp0"
title NARI - Instalar detector visual JJS

if not exist ".venv\Scripts\python.exe" (
  echo No encuentro .venv\Scripts\python.exe.
  echo Abre la carpeta de NARI y confirma que la instalacion principal este completa.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"
echo.
echo Instalando el detector local YOLO en el entorno de NARI...
echo Esta instalacion puede descargar dependencias grandes de PyTorch.
python -m pip install ultralytics
if errorlevel 1 (
  echo.
  echo No se pudo instalar Ultralytics. NARI seguira usando la vision VLM como respaldo.
  pause
  exit /b 1
)

echo.
echo Descargando/preparando el modelo compacto YOLOv8n...
python -c "from ultralytics import YOLO; m=YOLO('yolov8n.pt'); print('Modelo listo:', type(m).__name__)"
if errorlevel 1 (
  echo.
  echo La libreria se instalo, pero el modelo no pudo cargarse.
  echo Comprueba tu conexion y vuelve a ejecutar este archivo.
  pause
  exit /b 1
)

echo.
echo Detector instalado. Cierra NARI y vuelve a abrirla.
echo NARI usara YOLO para proponer avatares y el modelo visual para interpretar el combate.
pause
