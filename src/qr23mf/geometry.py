"""Geometry builder.

Translates a :class:`~qr23mf.qr.QrMatrix` plus a :class:`GeometryParams`
struct (and optional placement, module style, and text labels) into two
explicit triangle meshes suitable for STL / 3MF export:

* ``base`` — a flat axis-aligned box (the printable substrate).
* ``features`` — QR modules (square boxes or cylindrical dots) plus any
  rasterized text labels, stacked on top of the base.

All geometry is deterministic: for identical inputs the returned meshes are
byte-identical. Coordinates use ``float32`` to match ``numpy-stl``'s native
on-disk precision; the resulting STL/3MF files are stable across runs on the
same platform.

Traces: FR-2, FR-4, NFR-1, NFR-2.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
import numpy.typing as npt
from PIL import Image, ImageDraw, ImageFont
from stl.mesh import Mesh

from qr23mf.qr import QrMatrix

__all__ = [
    "MIN_MODULE_MM",
    "GeometryParams",
    "ModuleStyle",
    "QrPlacement",
    "TextLabel",
    "build_meshes",
]

#: Smallest per-module edge length (millimeters) we still accept. Anything
#: smaller almost certainly means the user gave us incompatible inputs
#: (``size_mm`` / ``qr_size_mm`` too small for the QR version + quiet zone).
#: 50 micron is well below any consumer FDM nozzle, so nobody hits this by
#: accident.
MIN_MODULE_MM: Final[float] = 0.05

#: Visual style for dark QR modules.
ModuleStyle = Literal["square", "dot"]

_VALID_STYLES: Final[frozenset[str]] = frozenset(("square", "dot"))

#: Regular-polygon sides used to approximate a dot. 16-gon gives a visibly
#: round print at a modest triangle count (4 * 16 - 4 = 60 triangles/dot).
_DOT_POLYGON_SIDES: Final[int] = 16

#: Pixel height used when rasterizing text via Pillow. Text is then scaled to
#: the requested ``height_mm`` (each raster pixel becomes a tiny extruded box
#: of edge ``height_mm / height_px``).
_TEXT_PX_HEIGHT: Final[int] = 32

_FloatArray = npt.NDArray[np.float32]
_BoolArray = npt.NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class GeometryParams:
    """Physical dimensions for the printed artifact.

    All lengths are in millimeters. Defaults produce a 60x60x2 mm plate with
    1 mm extruded code modules and a standard 4-module quiet zone (as
    recommended by the QR Code specification).

    ``depth_mm`` generalizes the plate to a rectangular footprint
    (``size_mm`` X ``depth_mm``); when ``None``, the plate is square and the
    value defaults to ``size_mm``.
    """

    size_mm: float = 60.0
    base_height_mm: float = 2.0
    pixel_height_mm: float = 1.0
    quiet_zone_modules: int = 4
    depth_mm: float | None = None

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
        if self.depth_mm is not None and self.depth_mm <= 0:
            raise ValueError(f"depth_mm must be > 0 or None, got {self.depth_mm}")

    @property
    def effective_depth_mm(self) -> float:
        """Plate Y extent in millimeters (= ``size_mm`` when ``depth_mm is None``)."""
        return float(self.size_mm) if self.depth_mm is None else float(self.depth_mm)

    def module_mm(self, matrix_size: int, qr_size_mm: float | None = None) -> float:
        """Per-module edge length for a QR of the given module count.

        When ``qr_size_mm`` is supplied the QR occupies that side length
        (used with :class:`QrPlacement`). Otherwise the QR fills the largest
        square inscribed in the plate (``min(size_mm, effective_depth_mm)``),
        which equals ``size_mm`` for the default square plate.
        """
        total = matrix_size + 2 * self.quiet_zone_modules
        effective = (
            float(qr_size_mm)
            if qr_size_mm is not None
            else min(float(self.size_mm), self.effective_depth_mm)
        )
        return effective / float(total)


@dataclass(frozen=True, slots=True)
class QrPlacement:
    """Position and size of the QR code within the base plate.

    All values are in millimeters. Offsets are measured from the plate's XY
    center. ``qr_size_mm`` covers the entire QR footprint **including** the
    quiet-zone margin, mirroring the existing ``GeometryParams.size_mm``
    semantics.
    """

    qr_size_mm: float | None = None
    x_offset_mm: float = 0.0
    y_offset_mm: float = 0.0

    def __post_init__(self) -> None:
        if self.qr_size_mm is not None and self.qr_size_mm <= 0:
            raise ValueError(f"qr_size_mm must be > 0 or None, got {self.qr_size_mm}")


@dataclass(frozen=True, slots=True)
class TextLabel:
    """A text label extruded above the plate at a specified position.

    ``(x_mm, y_mm)`` is the center of the rendered text's bounding box,
    measured from the plate's XY center. ``height_mm`` is the target text
    height; ``extrusion_mm`` is how far above the plate's top face the text
    rises.
    """

    content: str
    x_mm: float
    y_mm: float
    height_mm: float
    extrusion_mm: float

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content:
            raise ValueError("text label content must be a non-empty string")
        if self.height_mm <= 0:
            raise ValueError(f"text label height_mm must be > 0, got {self.height_mm}")
        if self.extrusion_mm <= 0:
            raise ValueError(f"text label extrusion_mm must be > 0, got {self.extrusion_mm}")


# ---------------------------------------------------------------------------
# Mesh primitives
# ---------------------------------------------------------------------------


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


def _regular_polygon_xy(cx: float, cy: float, radius: float, sides: int) -> _FloatArray:
    """CCW-from-above vertices of a regular polygon inscribed in a circle.

    Starts at angle ``pi/sides`` so a 4-gon is aligned like a diamond rather
    than an axis-aligned square; that's mostly cosmetic but keeps dot-style
    QRs from looking like rotated square-style QRs when ``sides`` is small.
    """
    offset = np.float32(np.pi / sides)
    angles = np.arange(sides, dtype=np.float32) * np.float32(2.0 * np.pi / sides) + offset
    xs = np.float32(cx) + np.float32(radius) * np.cos(angles)
    ys = np.float32(cy) + np.float32(radius) * np.sin(angles)
    return np.stack([xs, ys], axis=1).astype(np.float32)


def _extrude_prism(polygon_xy: _FloatArray, z0: float, z1: float) -> _FloatArray:
    """Extrude a convex CCW-from-above polygon into a prism with outward normals.

    Returns ``(4*N - 4, 3, 3)`` triangles: ``N-2`` bottom fan + ``N-2`` top fan
    + ``2N`` side-wall triangles.
    """
    if polygon_xy.ndim != 2 or polygon_xy.shape[1] != 2:
        raise ValueError(f"polygon must be (N, 2), got shape {polygon_xy.shape!r}")
    n = int(polygon_xy.shape[0])
    if n < 3:
        raise ValueError(f"polygon must have at least 3 vertices, got {n}")
    if z1 <= z0:
        raise ValueError(f"z1 ({z1}) must be > z0 ({z0})")

    xs = polygon_xy[:, 0].astype(np.float32, copy=False)
    ys = polygon_xy[:, 1].astype(np.float32, copy=False)
    z0_col = np.full(n, np.float32(z0), dtype=np.float32)
    z1_col = np.full(n, np.float32(z1), dtype=np.float32)
    bottom = np.stack([xs, ys, z0_col], axis=1).astype(np.float32)
    top = np.stack([xs, ys, z1_col], axis=1).astype(np.float32)

    triangles = np.zeros((4 * n - 4, 3, 3), dtype=np.float32)

    # Bottom face: outward normal -Z. Viewed from -Z, our CCW-from-above
    # vertices appear CW; fan-triangulate as (v0, v[i+1], v[i]).
    for i in range(1, n - 1):
        triangles[i - 1] = np.stack([bottom[0], bottom[i + 1], bottom[i]])

    # Top face: outward normal +Z. Fan-triangulate CCW as (v0, v[i], v[i+1]).
    top_offset = n - 2
    for i in range(1, n - 1):
        triangles[top_offset + i - 1] = np.stack([top[0], top[i], top[i + 1]])

    # Side walls: each edge i -> (i+1) % n is a quad
    # (bottom[i], bottom[next], top[next], top[i]) with outward normal.
    side_offset = 2 * (n - 2)
    for i in range(n):
        nxt = (i + 1) % n
        triangles[side_offset + 2 * i] = np.stack([bottom[i], bottom[nxt], top[nxt]])
        triangles[side_offset + 2 * i + 1] = np.stack([bottom[i], top[nxt], top[i]])

    return triangles


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


# ---------------------------------------------------------------------------
# Text rasterization (Pillow)
# ---------------------------------------------------------------------------


def _rasterize_text_to_grid(content: str, height_mm: float) -> tuple[_BoolArray, float]:
    """Rasterize ``content`` into a boolean grid using Pillow's default font.

    Returns ``(grid, cell_mm)`` where ``grid[row, col]`` is ``True`` for a
    dark cell and row 0 is the visual top of the text. ``cell_mm`` is the
    edge length (millimeters) of a single rasterization cell, chosen so the
    rendered text is exactly ``height_mm`` millimeters tall.
    """
    try:
        font = ImageFont.load_default(size=_TEXT_PX_HEIGHT)
    except TypeError:  # pragma: no cover - only hit on Pillow < 10.1
        font = ImageFont.load_default()

    bbox = font.getbbox(content)
    left, top, right, bottom = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
    width_px = max(1, right - left)
    height_px = max(1, bottom - top)

    # Mode "L" then threshold so we get a reliable boolean grid regardless of
    # how Pillow represents 1-bit images in the numpy bridge.
    img = Image.new("L", (width_px, height_px), color=0)
    draw = ImageDraw.Draw(img)
    draw.text((-left, -top), content, fill=255, font=font)

    arr = np.asarray(img, dtype=np.uint8) > 127
    cell_mm = float(height_mm) / float(height_px)
    return np.asarray(arr, dtype=np.bool_), cell_mm


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_meshes(
    matrix: QrMatrix,
    params: GeometryParams,
    *,
    placement: QrPlacement | None = None,
    module_style: ModuleStyle = "square",
    text_labels: Sequence[TextLabel] = (),
) -> tuple[Mesh, Mesh]:
    """Build the base plate and feature meshes for a QR code.

    The meshes are centered on the XY origin with the base's bottom face on
    ``z = 0``. Row 0 of ``matrix.modules`` is placed along ``+Y`` (top of
    the printed plate when viewed from ``+Z``).

    Args:
        matrix: QR module matrix produced by :func:`qr23mf.qr.build_matrix`.
        params: Plate dimensions. See :class:`GeometryParams`.
        placement: Optional QR size and offset within the plate. When ``None``
            the QR is centered and fills the largest square inscribed in the
            plate, preserving the original single-argument behavior.
        module_style: ``"square"`` (axis-aligned box per dark module, default)
            or ``"dot"`` (cylindrical prism per dark module).
        text_labels: Optional iterable of :class:`TextLabel` to extrude on the
            plate top. Each label is rasterized with Pillow; dark pixels are
            extruded as tiny axis-aligned boxes.

    Returns:
        ``(base_mesh, features_mesh)``. ``features_mesh`` contains the QR
        modules plus any text labels merged together; it may be empty if the
        QR has no dark modules (never happens for a real QR) **and** no text
        labels are supplied.

    Raises:
        ValueError: If the per-module edge would fall below
            :data:`MIN_MODULE_MM`, if ``module_style`` is unknown, or if the
            QR or any text label would extend outside the plate.
    """
    if module_style not in _VALID_STYLES:
        raise ValueError(
            f"module_style must be one of {sorted(_VALID_STYLES)}, got {module_style!r}"
        )

    qr_size_mm_opt = placement.qr_size_mm if placement is not None else None
    module_mm = params.module_mm(matrix.size, qr_size_mm_opt)
    if module_mm < MIN_MODULE_MM:
        total = matrix.size + 2 * params.quiet_zone_modules
        raise ValueError(
            f"Per-module edge {module_mm:.4f} mm is too small "
            f"(below {MIN_MODULE_MM} mm minimum) for a {matrix.size}-module "
            f"QR with a {params.quiet_zone_modules}-module quiet zone "
            f"({total} modules total)."
        )

    half_w = float(params.size_mm) / 2.0
    half_d = params.effective_depth_mm / 2.0

    qr_footprint_mm = (
        float(qr_size_mm_opt)
        if qr_size_mm_opt is not None
        else min(float(params.size_mm), params.effective_depth_mm)
    )
    x_offset = float(placement.x_offset_mm) if placement is not None else 0.0
    y_offset = float(placement.y_offset_mm) if placement is not None else 0.0
    qr_half = qr_footprint_mm / 2.0

    # Allow sub-micron numerical slop so a centered, plate-filling QR passes.
    tol = 1e-6
    if (
        x_offset - qr_half < -half_w - tol
        or x_offset + qr_half > half_w + tol
        or y_offset - qr_half < -half_d - tol
        or y_offset + qr_half > half_d + tol
    ):
        raise ValueError(
            f"QR footprint (size={qr_footprint_mm:g} mm, "
            f"offset=({x_offset:g}, {y_offset:g}) mm) extends outside the "
            f"{params.size_mm:g} x {params.effective_depth_mm:g} mm plate."
        )

    # --- Base plate ---
    base_triangles = _extrude_axis_aligned_box(
        x0=-half_w,
        y0=-half_d,
        z0=0.0,
        x1=+half_w,
        y1=+half_d,
        z1=params.base_height_mm,
    )
    base_mesh = _triangles_to_mesh(base_triangles)

    # --- QR modules ---
    dark_rows, dark_cols = np.nonzero(matrix.modules)

    z_top_base = np.float32(params.base_height_mm)
    z_top_feature = z_top_base + np.float32(params.pixel_height_mm)

    module_mm_f32 = np.float32(module_mm)
    quiet_offset_mm = np.float32(params.quiet_zone_modules) * module_mm_f32
    qr_left = np.float32(x_offset - qr_half)
    qr_top = np.float32(y_offset + qr_half)

    feature_chunks: list[_FloatArray] = []

    if dark_rows.size > 0:
        rows_f = dark_rows.astype(np.float32)
        cols_f = dark_cols.astype(np.float32)
        x0s = qr_left + quiet_offset_mm + cols_f * module_mm_f32
        x1s = x0s + module_mm_f32
        y1s = qr_top - quiet_offset_mm - rows_f * module_mm_f32
        y0s = y1s - module_mm_f32

        if module_style == "square":
            pixel_tris = np.zeros((dark_rows.size * 12, 3, 3), dtype=np.float32)
            for i in range(dark_rows.size):
                pixel_tris[i * 12 : (i + 1) * 12] = _extrude_axis_aligned_box(
                    float(x0s[i]),
                    float(y0s[i]),
                    float(z_top_base),
                    float(x1s[i]),
                    float(y1s[i]),
                    float(z_top_feature),
                )
            feature_chunks.append(pixel_tris)
        else:  # "dot"
            radius = module_mm / 2.0
            tris_per_dot = 4 * _DOT_POLYGON_SIDES - 4
            pixel_tris = np.zeros((dark_rows.size * tris_per_dot, 3, 3), dtype=np.float32)
            for i in range(dark_rows.size):
                cx = (float(x0s[i]) + float(x1s[i])) / 2.0
                cy = (float(y0s[i]) + float(y1s[i])) / 2.0
                poly = _regular_polygon_xy(cx, cy, radius, _DOT_POLYGON_SIDES)
                pixel_tris[i * tris_per_dot : (i + 1) * tris_per_dot] = _extrude_prism(
                    poly, float(z_top_base), float(z_top_feature)
                )
            feature_chunks.append(pixel_tris)

    # --- Text labels ---
    for label in text_labels:
        grid, cell_mm = _rasterize_text_to_grid(label.content, label.height_mm)
        if grid.size == 0:
            continue
        n_rows, n_cols = int(grid.shape[0]), int(grid.shape[1])
        width_mm = n_cols * cell_mm
        text_height_mm = n_rows * cell_mm

        lx0 = float(label.x_mm) - width_mm / 2.0
        lx1 = float(label.x_mm) + width_mm / 2.0
        ly_top = float(label.y_mm) + text_height_mm / 2.0
        ly_bot = float(label.y_mm) - text_height_mm / 2.0
        if (
            lx0 < -half_w - tol
            or lx1 > half_w + tol
            or ly_bot < -half_d - tol
            or ly_top > half_d + tol
        ):
            raise ValueError(
                f"Text label {label.content!r} "
                f"(bounds {width_mm:g} x {text_height_mm:g} mm at "
                f"({label.x_mm:g}, {label.y_mm:g})) extends outside the "
                f"{params.size_mm:g} x {params.effective_depth_mm:g} mm plate."
            )

        dark_r, dark_c = np.nonzero(grid)
        if dark_r.size == 0:
            continue

        cell_f32 = np.float32(cell_mm)
        lx0_f = np.float32(lx0)
        ly_top_f = np.float32(ly_top)
        z_text_top = z_top_base + np.float32(label.extrusion_mm)

        rows_f = dark_r.astype(np.float32)
        cols_f = dark_c.astype(np.float32)
        tx0s = lx0_f + cols_f * cell_f32
        tx1s = tx0s + cell_f32
        ty1s = ly_top_f - rows_f * cell_f32
        ty0s = ty1s - cell_f32

        text_tris = np.zeros((dark_r.size * 12, 3, 3), dtype=np.float32)
        for i in range(dark_r.size):
            text_tris[i * 12 : (i + 1) * 12] = _extrude_axis_aligned_box(
                float(tx0s[i]),
                float(ty0s[i]),
                float(z_top_base),
                float(tx1s[i]),
                float(ty1s[i]),
                float(z_text_top),
            )
        feature_chunks.append(text_tris)

    # --- Merge into features mesh ---
    if not feature_chunks:
        features_mesh = _triangles_to_mesh(np.zeros((0, 3, 3), dtype=np.float32))
    elif len(feature_chunks) == 1:
        features_mesh = _triangles_to_mesh(feature_chunks[0])
    else:
        features_mesh = _triangles_to_mesh(np.concatenate(feature_chunks, axis=0))

    return base_mesh, features_mesh
