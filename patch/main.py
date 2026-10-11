from __future__ import annotations

import subprocess
import sys
import traceback
from pathlib import Path


def _ensure_mss_available() -> None:
    """Repair the screen-capture dependency when an update preserves an old .venv."""
    try:
        import mss  # noqa: F401
        return
    except Exception as initial_error:
        print(f"Dependencia de captura de pantalla no disponible: {initial_error}")

    print("Reparando la dependencia MSS de NARI...")
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--upgrade",
                "mss>=10.0.0",
            ],
            check=True,
        )
        import mss  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "No se pudo instalar MSS para la captura de pantalla. "
            "Comprueba la conexión a internet y ejecuta NARI_INSTALAR.bat."
        ) from exc


def main() -> int:
    try:
        _ensure_mss_available()
        from nari.app import NariApp

        app = NariApp()
        app.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
