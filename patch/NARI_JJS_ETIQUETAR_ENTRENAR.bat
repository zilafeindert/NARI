@echo off
setlocal
cd /d "%~dp0"
title NARI - Aprender detector JJS

if not exist ".venv\Scripts\python.exe" (
  echo No encuentro .venv\Scripts\python.exe. Comprueba la instalacion de NARI.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"
echo.
echo IMPORTANTE: cierra NARI antes de entrenar para liberar memoria/GPU.
echo Primero etiqueta las capturas guardadas con F9 y despues empieza el entrenamiento.
echo Se requieren al menos 20 imagenes con enemigos etiquetados; 50-150 es mejor.
echo.
python -m nari.jjs_training label-train
if errorlevel 1 (
  echo.
  echo No se pudo completar el entrenamiento. Lee el mensaje anterior.
  pause
  exit /b 1
)
echo.
echo Detector entrenado. Vuelve a abrir NARI para utilizarlo.
pause
