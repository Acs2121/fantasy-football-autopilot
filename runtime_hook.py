"""
PyInstaller runtime hook — fixes resource paths when running from a frozen .exe.

Imported automatically before desktop.py runs (listed in build.spec hookspath).
Sets BASE_DIR so Flask can find templates/, static/, and data/ next to the exe.
"""
import os
import sys

# When frozen, sys._MEIPASS is the temp directory PyInstaller unpacks to.
# We patch the working directory so relative paths in Flask still resolve.
if getattr(sys, 'frozen', False):
    os.chdir(sys._MEIPASS)
