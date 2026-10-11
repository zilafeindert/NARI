@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Instalar o reparar NARI

echo.
echo ============================================
echo          INSTALADOR DE NARI
echo ============================================
echo.
echo Este instalador conserva data, models, voices y .env.
echo Antes de continuar, cierra NARI y cualquier instalador pip.
echo Esto evita errores de acceso denegado con cv2.pyd.
echo.
choice /C SN /N /M "NARI esta completamente cerrada? [S/N] "
if errorlevel 2 exit /b 1

if not exist "nari\app.py" (
  echo ERROR: No encuentro nari\app.py.
  echo Extrae TODO el contenido del ZIP de NARI en esta carpeta antes de instalar.
  goto failed
)
if not exist "main.py" (
  echo ERROR: Falta main.py. Descarga el ZIP completo de NARI otra vez.
  goto failed
)
if not exist "requirements.txt" (
  echo ERROR: Falta requirements.txt. Descarga el ZIP completo de NARI otra vez.
  goto failed
)

set "PYTHON_CMD="
call :detect_python
if defined PYTHON_CMD goto python_ready

echo No se encontro una instalacion funcional de Python 3.11, 3.12, 3.13 o 3.14.
echo Se intentara instalar Python 3.14 mediante Python Install Manager.
choice /C SN /N /M "Intentar instalar Python 3.14 ahora? [S/N] "
if errorlevel 2 goto failed
where pymanager >nul 2>&1
if not errorlevel 1 (
  pymanager install 3.14
) else (
  where py >nul 2>&1
  if not errorlevel 1 (
    py install 3.14
  ) else (
    echo No encuentro Python Install Manager.
    echo Instala Python desde https://www.python.org/downloads/windows/
    goto failed
  )
)
call :detect_python
if not defined PYTHON_CMD (
  echo No se pudo iniciar una version compatible de Python.
  echo Instala Python 3.14 desde https://www.python.org/downloads/windows/ y vuelve a intentarlo.
  goto failed
)

:python_ready
echo.
echo Python base detectado: %PYTHON_CMD%

rem Si el entorno existe pero apunta a un Python eliminado, conservarlo como respaldo
rem y crear un .venv limpio. Solo se mueve .venv; los datos personales no se tocan.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
  if errorlevel 1 goto backup_venv
  goto install_deps
)
if exist ".venv" goto backup_venv
goto create_venv

:backup_venv
echo.
echo El entorno .venv anterior no puede arrancar. Se guardara como respaldo.
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%T"
if not defined STAMP set "STAMP=%RANDOM%"
ren ".venv" ".venv_backup_%STAMP%"
if errorlevel 1 (
  echo No se pudo renombrar .venv. Asegurate de que NARI este cerrada y vuelve a intentarlo.
  goto failed
)

:create_venv
echo.
echo Creando el entorno virtual de NARI...
%PYTHON_CMD% -m venv ".venv"
if errorlevel 1 (
  echo No se pudo crear .venv con %PYTHON_CMD%.
  goto failed
)

:install_deps
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Python no creo .venv correctamente.
  goto failed
)

echo.
echo Actualizando pip...
".venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 (
  echo No se pudo inicializar pip en el entorno virtual.
  goto failed
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo No se pudo actualizar pip. Revisa la conexion a internet.
  goto failed
)

echo.
echo Instalando dependencias principales de NARI...
".venv\Scripts\python.exe" -m pip install -r "requirements.txt"
if errorlevel 1 (
  echo.
  echo ERROR instalando dependencias.
  echo Comprueba internet, cierra NARI y vuelve a ejecutar el instalador.
  echo No se han borrado data, models, voices ni .env.
  goto failed
)

echo.
echo Verificando que la aplicacion pueda cargarse...
".venv\Scripts\python.exe" -c "import nari.app; print('Importacion principal correcta')"
if errorlevel 1 (
  echo ERROR: las dependencias se instalaron, pero NARI no pudo importarse.
  goto failed
)

echo.
echo ============================================
echo NARI quedo instalada o reparada.
echo Se conservaron data, models, voices y .env.
echo ============================================
echo.
choice /C SN /N /M "Iniciar NARI ahora? [S/N] "
if errorlevel 2 goto success
start "" "%CD%\.venv\Scripts\python.exe" "%CD%\main.py"

:success
echo.
echo Ya puedes usar NARI_INICIAR.bat para iniciar NARI.
pause
exit /b 0

:failed
echo.
echo La instalacion no se completo. No borres tus carpetas de datos.
pause
exit /b 1

:detect_python
set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 (
  for %%V in (3.14 3.13 3.12 3.11) do call :try_py_version %%V
)
if defined PYTHON_CMD exit /b 0
where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] < (3,15) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=python"
)
exit /b 0

:try_py_version
if defined PYTHON_CMD exit /b 0
py -%~1 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -%~1"
exit /b 0
