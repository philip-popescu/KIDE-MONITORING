"""Run: python main.py [--simulate] [--config path.json]."""

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from kide_monitor.config import ConfigError, zonepoint_config, load_config


def main():
    # One-file bundles extract __file__ to a temporary directory. Persist user
    # settings and logs beside the executable, not in that temporary directory.
    app_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    parser = argparse.ArgumentParser(description="Kidde 2X-A input/output monitor")
    parser.add_argument("--config", type=Path, default=app_dir / "config.json")
    parser.add_argument("--simulate", action="store_true", help="Start clearly labeled synthetic monitoring")
    parser.add_argument("--validate-config", action="store_true", help="Validate the selected configuration and exit")
    args = parser.parse_args()
    try:
        config = load_config(args.config) if args.config.exists() else zonepoint_config()
        if args.validate_config:
            if not args.config.exists():
                raise ConfigError(f"Configuration file not found: {args.config}")
            print(f"Valid configuration: {args.config}")
            return 0
        logs = app_dir / "logs"
        logs.mkdir(exist_ok=True)
        handler = RotatingFileHandler(logs / "monitor.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        from kide_monitor.ui import MonitorApp
        app = MonitorApp(config, args.config, simulate=args.simulate)
        app.mainloop()
        return 0
    except (ConfigError, OSError, ImportError) as exc:
        print(f"Cannot start KIDE Monitoring: {exc}", file=sys.stderr)
        if isinstance(exc, ImportError):
            print("Install the dependencies with: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
