from pathlib import Path
import sys

from vrc_time_together.qt_app import main


if __name__ == "__main__":
    script_path = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
    raise SystemExit(main(script_path))
