"""Packaged static assets for qr23mf.

Currently holds ``icon.png`` \u2014 a QR code that encodes the project's GitHub
URL, used as the Tk window / macOS Dock icon so the GUI stops showing the
Python rocket on launch. Loaded at runtime via :mod:`importlib.resources`
so the file travels with the wheel and sdist.
"""

from __future__ import annotations

__all__: list[str] = []
