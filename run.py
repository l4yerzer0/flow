import sys
import os
import subprocess
from pathlib import Path
from importlib import metadata

def _read_required_distributions(requirements_path: Path) -> list[str]:
    """Read distribution names from requirements.txt (ignores comments/flags)."""
    packages: list[str] = []
    for raw in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue

        # Keep only package/distribution name before any version specifier or marker.
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", ";"):
            if sep in line:
                line = line.split(sep, 1)[0].strip()
        if "[" in line:
            line = line.split("[", 1)[0].strip()

        if line:
            packages.append(line)
    return packages

def check_and_install_dependencies():
    """Checks all requirements distributions and installs if something is missing."""
    requirements_path = Path(__file__).with_name("requirements.txt")
    if not requirements_path.exists():
        print("requirements.txt not found. Skipping dependency auto-install.")
        return

    required_distributions = _read_required_distributions(requirements_path)
    missing: list[str] = []

    for dist_name in required_distributions:
        try:
            metadata.version(dist_name)
        except metadata.PackageNotFoundError:
            missing.append(dist_name)

    if missing:
        print(f"Missing dependencies detected: {', '.join(missing)}")
        print("Installing dependencies from requirements.txt...")
        try:
            # Use sys.executable to ensure we install in the CURRENT python environment (e.g. py -3.12)
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(requirements_path)])
            print("Dependencies installed successfully.\n")
        except subprocess.CalledProcessError as e:
            print(f"Error installing dependencies: {e}")
            sys.exit(1)

if __name__ == "__main__":
    # 1. Auto-install dependencies before importing app logic
    check_and_install_dependencies()

    # 2. Setup path
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))

    # 3. Import App (Delayed import so it doesn't fail before installation)
    try:
        from src.ui.app import Flow
        app = Flow()
        app.run()
    except ImportError as e:
        print(f"Critical Error: Failed to import application after dependency check.\n{e}")
        input("Press Enter to exit...")
