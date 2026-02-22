import sys
import os
import subprocess
import importlib.util

def check_and_install_dependencies():
    """Checks for required packages and installs them if missing."""
    required_packages = ['textual', 'aiohttp', 'pydantic', 'dotenv']
    missing = []

    for package in required_packages:
        # Map import name to package name if they differ (e.g. dotenv -> python-dotenv)
        import_name = package
        if package == 'dotenv':
            import_name = 'dotenv' # actually import dotenv checks for python-dotenv usually
        
        if importlib.util.find_spec(import_name) is None:
            missing.append(package)

    if missing:
        print(f"Missing dependencies detected: {', '.join(missing)}")
        print("Installing dependencies from requirements.txt...")
        try:
            # Use sys.executable to ensure we install in the CURRENT python environment (e.g. py -3.12)
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
            print("Dependencies installed successfully.\n")
        except subprocess.CalledProcessError as e:
            print(f"Error installing dependencies: {e}")
            sys.exit(1)
    else:
        # Quick check passed, but let's ensure specific versions aren't broken
        pass

if __name__ == "__main__":
    # 1. Auto-install dependencies before importing app logic
    check_and_install_dependencies()

    # 2. Setup path
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))

    # 3. Import App (Delayed import so it doesn't fail before installation)
    try:
        from src.ui.app import TradingBotApp
        app = TradingBotApp()
        app.run()
    except ImportError as e:
        print(f"Critical Error: Failed to import application after dependency check.\n{e}")
        input("Press Enter to exit...")
