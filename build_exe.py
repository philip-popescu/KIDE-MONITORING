r"""Build dist/KIDE_Monitoring.exe using PyInstaller's one-file mode.

Run with the project environment: .venv\Scripts\python.exe build_exe.py
Add --console to retain a console for troubleshooting.
"""

import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="Build the KIDE Monitoring executable")
    parser.add_argument("--console", action="store_true", help="Keep a console window for debugging")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent

    missing = [name for name in ("PyInstaller", "customtkinter", "pymodbus")
               if importlib.util.find_spec(name) is None]
    if missing:
        print(f"Missing build dependencies: {', '.join(missing)}", file=sys.stderr)
        prefix = "& " if sys.platform == "win32" else ""
        print(f'Install with the same Python: {prefix}"{sys.executable}" -m pip install '
              f'-r "{root / "requirements.txt"}" pyinstaller', file=sys.stderr)
        return 1

    build_dir = root / "build"
    build_dir.mkdir(exist_ok=True)
    command = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--console" if args.console else "--windowed",
        "--noconfirm",
        "--clean",
        "--name", "KIDE_Monitoring",
        "--distpath", str(root / "dist"),
        "--workpath", str(build_dir / "pyinstaller"),
        "--specpath", str(build_dir),
        # Include the library's themes, fonts, icons, and Python modules.
        "--collect-all", "customtkinter",
        str(root / "main.py"),
    ]
    print("Building KIDE Monitoring as a single executable...", flush=True)
    try:
        subprocess.run(command, cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1

    executable = root / "dist" / ("KIDE_Monitoring.exe" if sys.platform == "win32" else "KIDE_Monitoring")
    print(f"\nBuilt: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
