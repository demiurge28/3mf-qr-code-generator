"""Geometry builder.

Translates a :class:`~qr23mf.qr.QrMatrix` plus a :class:`GeometryParams`
struct into two explicit triangle meshes suitable for STL / 3MF export:

* ``base`` — a flat axis-aligned box (the printable substrate).
* ``pixels`` — one axis-aligned box per dark module, stacked on the base top
  with the configured extrusion height. Quiet-zone padding is subtracted from
  the usable area so the code prints with proper blank margins.

All geometry is deterministic: for identical ``(matrix, params)`` inputs the
returned meshes are byte-identical. Coordinates use ``float32`` to match
``numpy-stl``'s native on-disk precision; the resulting STL/3MF files are
stable across runs on the same platform.

Traces: FR-2, FR-4, NFR-1, NFR-2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt
from stl.mesh import Mesh

from qr23mf.qr import QrMatrix

__all__ = ["MIN_MODULE_MM", "GeometryParams", "build_meshes"]

#: Smallest per-module edge length (millimeters) we still accept. Anything
#: smaller almost certainly means the user gave us incompatible inputs
#: (``size_mm`` too small for the QR version + quiet zone). 50 micron is
#: well below any consumer FDM nozzle, so nobody hits this by accident.
MIN_MODULE_MM: Final[float] = 0.05

_FloatArray = npt.NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class GeometryParams:
    """Physical dimensions for the printed artifact.

    All lengths are in millimeters. Defaults produce a 60x60x2 mm plate with
    1 mm extruded code modules and a standard 4-module quiet zone (as
    recommended by the QR Code specification).
    """

    size_mm: float = 60.0
    base_height_mm: float = 2.0
    pixel_height_mm: float = 1.0
    quiet_zone_modules: int = 4

    def __post_init__(self) -> None:
        """Validate inputs. :class:`ValueError` if any dimension is non-positive."""
        if not isinstance(self.quiet_zone_modules, int) or isinstance(
            self.quiet_zone_modules, bool
        ):
            raise ValueError(
                f"quiet_zone_modules must be an int, got {type(self.quiet_zone_modules).__name__}"
            )
        if self.quiet_zone_modules < 0:
            raise ValueError(f"quiet_zone_modules must be >= 0, got {self.quiet_zone_modules}")
        if self.size_mm <= 0:
            raise ValueError(f"size_mm must be > 0, got {self.size_mm}")
        if self.base_height_mm <= 0:
            raise ValueError(f"base_height_mm must be > 0, got {self.base_height_mm}")
        if self.pixel_height_mm <= 0:
            raise ValueError(f"pixel_height_mm must be > 0, got {self.pixel_height_mm}")

    def module_mm(self, matrix_size: int) -> float:
        """Per-module edge length for a QR of the given module count."""
        total = matrix_size + 2 * self.quiet_zone_modules
        return float(self.size_mm) / float(total)


def _extrude_axis_aligned_box(
    x0: float, y0: float, z0: float, x1: float, y1: float, z1: float
) -> _FloatArray:
    """Build the 12 CCW-from-outside triangles of an axis-aligned box.

    Returns a ``(12, 3, 3)`` float32 array. ``x0 < x1``, ``y0 < y1``,
    ``z0 < z1`` is required; outward face normals are guaranteed by the
    winding order encoded below.
    """
    v0 = (x0, y0, z0)
    v1 = (x1, y0, z0)
    v2 = (x1, y1, z0)
    v3 = (x0, y1, z0)
    v4 = (x0, y0, z1)
    v5 = (x1, y0, z1)
    v6 = (x1, y1, z1)
    v7 = (x0, y1, z1)

    triangles: tuple[tuple[tuple[float, float, float], ...], ...] = (
        # Bottom (-Z)
        (v0, v2, v1),
        (v0, v3, v2),
        # Top (+Z)
        (v4, v5, v6),
        (v4, v6, v7),
        # Front (-Y)
        (v0, v1, v5),
        (v0, v5, v4),
        # Back (+Y)
        (v2, v3, v7),
        (v2, v7, v6),
        # Left (-X)
        (v0, v4, v3),
        (v4, v7, v3),
        # Right (+X)
        (v1, v2, v5),
        (v5, v2, v6),
    )
    return np.asarray(triangles, dtype=np.float32)


def _triangles_to_mesh(vectors: _FloatArray) -> Mesh:
    """Wrap a ``(N, 3, 3)`` triangle array in an :class:`stl.mesh.Mesh`.

    numpy-stl computes per-face normals automatically on construction, so the
    caller only needs to supply the vertex positions.
    """
    if vectors.ndim != 3 or vectors.shape[1:] != (3, 3):
        raise ValueError(f"Expected (N, 3, 3) triangle array, got shape {vectors.shape!r}")
    data = np.zeros(vectors.shape[0], dtype=Mesh.dtype)
    # Structured numpy arrays allow string field indexing; mypy's overload
    # stubs only model the integer-index path, so cast here.
    data["vectors"] = vectors  # type: ignore[call-overload]
    return Mesh(data)


def build_meshes(matrix: QrMatrix, params: GeometryParams) -> tuple[Mesh, Mesh]:
    """Build the base plate and pixel meshes for a QR code.

    The meshes are centered on the XY origin with the base's bottom face on
    ``z = 0``. Row 0 of ``matrix.modules`` is placed along ``+Y`` (top of
    the printed plate when viewed from ``+Z``).

    Args:
        matrix: QR module matrix produced by :func:`qr23mf.qr.build_matrix`.
        params: Physical dimensions. See :class:`GeometryParams`.

    Returns:
        ``(base_mesh, pixels_mesh)``. The pixel mesh may be empty if the
        matrix contains no dark modules -- this never happens for a valid QR
        code, but the function handles it gracefully by returning an empty
        mesh.

    Raises:
        ValueError: If the per-module edge length would fall below
            :data:`MIN_MODULE_MM` for the given matrix size and quiet zone.
    """
    module_mm = params.module_mm(matrix.size)
    if module_mm < MIN_MODULE_MM:
        total = matrix.size + 2 * params.quiet_zone_modules
        raise ValueError(
            f"size_mm={params.size_mm} is too small for a {matrix.size}-module "
            f"QR with a {params.quiet_zone_modules}-module quiet zone "
            f"({total} modules total): each module would be {module_mm:.4f} mm "
            f"which is below the {MIN_MODULE_MM} mm minimum."
        )

    half = params.size_mm / 2.0

    # --- Base plate ---
    base_triangles = _extrude_axis_aligned_box(
        x0=-half,
        y0=-half,
        z0=0.0,
        x1=+half,
        y1=+half,
        z1=params.base_height_mm,
    )
    base_mesh = _triangles_to_mesh(base_triangles)

    # --- Pixels ---
    dark_rows, dark_cols = np.nonzero(matrix.modules)
    if dark_rows.size == 0:
        pixels_mesh = _triangles_to_mesh(np.zeros((0, 3, 3), dtype=np.float32))
        return base_mesh, pixels_mesh

    # Convert integer indices to float32 to match mesh dtype.
    rows = dark_rows.astype(np.float32)
    cols = dark_cols.astype(np.float32)

    module_mm_f32 = np.float32(module_mm)
    quiet_offset_mm = np.float32(params.quiet_zone_modules) * module_mm_f32

    # Pixel box bounds in XY. Row 0 is at the TOP (+Y), so we flip on the y axis.
    x0 = np.float32(-half) + quiet_offset_mm + cols * module_mm_f32
    x1 = x0 + module_mm_f32
    y1 = np.float32(+half) - quiet_offset_mm - rows * module_mm_f32
    y0 = y1 - module_mm_f32
    z0 = np.float32(params.base_height_mm)
    z1 = z0 + np.float32(params.pixel_height_mm)

    # Stack each dark module's 12 triangles into a (N*12, 3, 3) array.
    n_pixels = dark_rows.size
    pixel_triangles = np.zeros((n_pixels * 12, 3, 3), dtype=np.float32)
    for i in range(n_pixels):
        pixel_triangles[i * 12 : (i + 1) * 12] = _extrude_axis_aligned_box(
            float(x0[i]),
            float(y0[i]),
            float(z0),
            float(x1[i]),
            float(y1[i]),
            float(z1),
        )

    pixels_mesh = _triangles_to_mesh(pixel_triangles)
    return base_mesh, pixels_mesh
