from __future__ import annotations

import traceback


def main() -> int:
    try:
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
