@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Reparar entorno Python de NARI

if not exist ".venv\pyvenv.cfg" (
  echo No encuentro un entorno virtual existente. Ejecuta NARI_INSTALAR.bat.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Falta .venv\Scripts\python.exe. Ejecuta NARI_INSTALAR.bat.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -c "import sys, requests" >nul 2>&1
if not errorlevel 1 (
  echo El entorno de Python funciona correctamente.
  exit /b 0
)

where py >nul 2>&1
if errorlevel 1 (
  echo No encuentro el comando py de Python Install Manager.
  echo Instala o repara Python 3.14 desde https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

rem Si Python 3.14 existe, re-enlazar el entorno mantiene los paquetes ya instalados.
py -3.14 -c "import sys" >nul 2>&1
if errorlevel 1 (
  echo No se encuentra una instalacion funcional de Python 3.14.
  echo NARI necesita reinstalar el runtime base; esto no borra data, voces, modelos ni memoria.
  choice /C SN /N /M "Instalar/reparar Python 3.14 ahora? [S/N] "
  if errorlevel 2 exit /b 1

  where pymanager >nul 2>&1
  if not errorlevel 1 (
    pymanager install 3.14
  ) else (
    py install 3.14
  )
  py -3.14 -c "import sys" >nul 2>&1
  if errorlevel 1 (
    echo La instalacion normal no arreglo el runtime. Se intentara reemplazar el runtime 3.14 registrado.
    where pymanager >nul 2>&1
    if not errorlevel 1 (
      pymanager install --force 3.14
    ) else (
      py install --force 3.14
    )
  )
)

py -3.14 -c "import sys" >nul 2>&1
if errorlevel 1 (
  echo No se pudo iniciar Python 3.14.
  echo Instala Python 3.14 desde https://www.python.org/downloads/windows/ y vuelve a ejecutar este archivo.
  pause
  exit /b 1
)

echo Reparando .venv con la instalacion disponible de Python 3.14...
py -3.14 -m venv --upgrade ".venv"
if errorlevel 1 (
  echo No se pudo reconstruir el lanzador del entorno virtual.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -c "import sys, requests" >nul 2>&1
if errorlevel 1 (
  echo Los paquetes del entorno no se pudieron cargar. Intentando restaurar requests para el actualizador...
  ".venv\Scripts\python.exe" -m pip install requests
)
".venv\Scripts\python.exe" -c "import sys, requests" >nul 2>&1
if errorlevel 1 (
  echo Python se reparo, pero faltan dependencias del actualizador.
  echo No se borraron tus datos. Revisa la instalacion de NARI.
  pause
  exit /b 1
)

echo Python de NARI reparado. Se conservaron los paquetes y archivos de datos existentes.
exit /b 0
