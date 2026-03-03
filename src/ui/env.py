import os

def is_termux() -> bool:
    """Check if the app is running in Termux."""
    return "TERMUX_VERSION" in os.environ

def is_mobile() -> bool:
    """Check if the app is likely running on a mobile device (Termux or similar)."""
    # Simple check for now, can be expanded
    return is_termux() or os.environ.get("MOBILE_UI") == "1"
