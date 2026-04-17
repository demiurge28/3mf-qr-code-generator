"""qr23mf — turn a string/URL into a 3D-printable QR code mesh (STL or 3MF).

This is the top-level package. Downstream scopes wire up real functionality
in submodules (``qr``, ``geometry``, ``writers``, ``cli``). At bootstrap time
only the CLI skeleton is exposed.
"""

from __future__ import annotations

from importlib import metadata as _metadata

__all__ = ["__version__"]

try:
    # The distribution is published as ``3mf-qr-code-generator``; the importable
    # module is ``qr23mf`` because Python identifiers cannot begin with a digit.
    __version__: str = _metadata.version("3mf-qr-code-generator")
except _metadata.PackageNotFoundError:  # pragma: no cover - only hit in uninstalled source tree
    __version__ = "0.0.0+local"
