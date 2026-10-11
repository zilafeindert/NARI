# NARI

NARI es un asistente/agente local para Windows con chat, voz, visión de pantalla, memoria persistente y perfiles de juego.

## Instalacion nueva o reparacion

1. Descarga el ZIP desde [Releases](https://github.com/zilafeindert/NARI/releases/latest).
2. Extrae **todo** el contenido del ZIP en una carpeta fija, por ejemplo `NARI`. No ejecutes archivos directamente desde la vista previa del ZIP.
3. Cierra NARI y cualquier instalador de Python que siga abierto.
4. Ejecuta `NARI_INSTALAR.bat`. El instalador detecta Python 3.11–3.14, crea o repara `.venv`, instala las dependencias e intenta validar que NARI se pueda cargar.
5. Al terminar, abre `NARI_INICIAR.bat`.

El instalador no elimina `data`, `models`, `voices` ni `.env`. Si encuentra un entorno virtual roto, lo conserva con el nombre `.venv_backup_...` y crea uno nuevo. Necesita conexión a internet para descargar Python o las dependencias si aún no están instalados.

Si aparece un error de acceso denegado en `cv2.pyd`, cierra NARI por completo y vuelve a ejecutar el instalador. No borres manualmente archivos dentro de `site-packages`.

## Detector opcional para JJS

`NARI_JJS_DETECTOR_INSTALAR.bat` instala Ultralytics/PyTorch, que es una dependencia opcional y grande. Cierra NARI antes de ejecutarlo. El detector requiere un modelo entrenado con capturas de JJS para reconocer de forma fiable los avatares estilizados.

## Actualizaciones

NARI consulta nuevas versiones del repositorio y puede instalar el ZIP publicado. La memoria y los recursos locales se conservan durante las actualizaciones.

## Componentes

- Chat y personalidad local
- Voz y escucha
- Visión de pantalla
- Automatización de teclado y ratón
- Memoria persistente
- Perfiles y aprendizaje de juego

Este repositorio es la fuente de distribución de NARI.
