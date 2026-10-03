from __future__ import annotations
import os
import sys
from nari.config import load_settings
from nari.updater import APP_VERSION, OFFICIAL_REPO, _is_newer, apply, check

def main() -> int:
    settings = load_settings()
    repo = str(settings.get("github_repo") or OFFICIAL_REPO).strip()
    try:
        info = check(repo=repo)
        latest = str(info.get("version", APP_VERSION))
        print(f"NARI {APP_VERSION} -> {latest}")
        if not _is_newer(latest, APP_VERSION):
            print("NARI ya está actualizada.")
            return 0
        print(info.get("notes", ""))
        zip_url = str(info.get("zip_url", ""))
        if not zip_url:
            print("La Release existe, pero no contiene un ZIP.")
            return 0
        auto = os.getenv("NARI_AUTO_UPDATE", "0") == "1" or "--auto" in sys.argv
        if not auto:
            answer = input("Escribe S para instalar la actualización: ").strip().lower()
            if answer != "s":
                print("Actualización cancelada.")
                return 0
        else:
            print("Actualización automática activada.")
        print(apply(zip_url, latest))
        return 0
    except Exception as exc:
        print("Actualizador NARI: no se pudo comprobar la actualización:", exc)
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
