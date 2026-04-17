"""STL writer.

Writes the base and feature meshes as a binary STL. The default *combined*
mode concatenates both into a single body (easiest for single-material
prints). The *split* mode writes two sibling files so the slicer imports
them as two independently selectable bodies and the user can assign a
distinct filament to each.

The formal ``stl-writer`` scope (see
``vbrief/proposed/2026-04-17-stl-writer.vbrief.json``) will extend this
with header metadata conventions, deterministic byte-level guarantees,
and dedicated tests.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from stl.mesh import Mesh

__all__ = ["features_split_path", "write_stl"]


def features_split_path(base_path: Path) -> Path:
    """Return the features-mesh path when writing in split mode.

    Inserts ``_features`` before the file's suffix, so ``coaster.stl``
    becomes ``coaster_features.stl``.
    """
    base_path = Path(base_path)
    stem = base_path.stem
    suffix = base_path.suffix or ".stl"
    return base_path.with_name(f"{stem}_features{suffix}")


def write_stl(base: Mesh, pixels: Mesh, path: Path, *, split: bool = False) -> list[Path]:
    """Write ``base`` and ``pixels`` as binary STL(s) and return the written paths.

    Args:
        base: Base plate mesh.
        pixels: Pixel (QR + text) feature mesh; may be empty.
        path: Destination path for the base STL. Parent directories are
            created if missing.
        split: When ``True``, write two STL files: ``path`` for the base and
            ``<stem>_features<suffix>`` alongside it for the features. This
            lets a slicer load each body as a selectable object (e.g. for
            per-filament assignment in a multi-material print). When
            ``False`` (default), both meshes are concatenated into a single
            binary STL at ``path`` for a single-material print.

    Returns:
        The list of file paths actually written, in write order.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if split and pixels.data.size > 0:
        base_copy = Mesh(base.data.copy())
        base_copy.save(str(path))
        features_path = features_split_path(path)
        features_copy = Mesh(pixels.data.copy())
        features_copy.save(str(features_path))
        return [path, features_path]

    if pixels.data.size == 0:
        combined = Mesh(base.data.copy())
    else:
        combined = Mesh(np.concatenate([base.data, pixels.data]))
    combined.save(str(path))
    return [path]
