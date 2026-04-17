"""Single-color STL writer (bootstrap primitive).

Merges the base and pixels meshes into a single binary STL on disk. The
formal ``stl-writer`` scope (see
``vbrief/proposed/2026-04-17-stl-writer.vbrief.json``) will extend this with
header metadata conventions, deterministic byte-level guarantees, and
dedicated tests. Today's implementation is just enough for the CLI's
``generate --out path.stl`` option.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from stl.mesh import Mesh

__all__ = ["write_stl"]


def write_stl(base: Mesh, pixels: Mesh, path: Path) -> None:
    """Write ``base`` and ``pixels`` as a single binary STL at ``path``.

    Args:
        base: Base plate mesh (12 triangles for an axis-aligned box).
        pixels: Pixel extrusions mesh (``N * 12`` triangles); may be empty.
        path: Destination path. Parent directories are created if missing.

    The two meshes are concatenated into one :class:`~stl.mesh.Mesh` so
    slicers see a single body. The file is written in numpy-stl's default
    binary STL format.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if pixels.data.size == 0:
        combined = Mesh(base.data.copy())
    else:
        combined = Mesh(np.concatenate([base.data, pixels.data]))

    combined.save(str(path))
