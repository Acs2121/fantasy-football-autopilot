"""
Fantasy Football Autopilot — Desktop launcher.

Opens the app in Edge/Chrome in "app mode" (no URL bar, standalone window).
Double-click this instead of running app.py + opening a browser manually.

Usage:
    python desktop.py
"""

from AutoPicker_Browser import app
from flaskwebgui import FlaskUI

if __name__ == "__main__":
    FlaskUI(
        app=app,
        server="flask",
        width=1400,
        height=900,
        fullscreen=False,
    ).run()
