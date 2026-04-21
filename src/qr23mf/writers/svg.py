"""SVG writer for qr23mf.

Emits a flat 2D SVG of the QR code (and optional text labels) in
millimetre units, suitable for laser etching / engraving / cutting
plates or for import into vector editors (Inkscape, Illustrator,
LightBurn). The geometry math mirrors :func:`qr23mf.geometry.build_meshes`
so the SVG output and the 3MF output describe the same design in XY;
only the Z extrusion step differs.

The resulting SVG uses ``width="<N>mm"`` / ``height="<M>mm"`` plus a
matching ``viewBox`` so it imports at real-world size in every vector
editor that honors SVG units.

Tracks: #3 (*feature: SVG writer for laser etching / engraving plates*).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final
from xml.sax.saxutils import escape

import numpy as np

from qr23mf.geometry import (
    MIN_MODULE_MM,
    GeometryParams,
    ModuleStyle,
    QrPlacement,
    TextLabel,
    _rasterize_text_to_grid,
)
from qr23mf.qr import QrMatrix

__all__ = ["svg_string", "write_svg"]

#: Valid ``module_style`` values. Kept in sync with :mod:`qr23mf.geometry`.
_VALID_STYLES: Final[frozenset[str]] = frozenset(("square", "dot"))

#: Number of decimal places used when formatting coordinates / sizes. Six
#: is the printf convention used by the 3MF writer and keeps sub-micron
#: precision without bloating the output.
_PRECISION: Final[int] = 6


def _fmt(value: float) -> str:
    """Format a float with :data:`_PRECISION` decimals, stripping zeros.

    ``f"{12.0:.6f}"`` is ``"12.000000"``; this helper shortens that to
    ``"12"`` so the SVG stays compact. Negative zero is normalised to
    plain ``0`` for determinism across runs.
    """
    if value == 0.0:
        return "0"
    out = f"{value:.{_PRECISION}f}"
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    return out or "0"


def _slugify(text: str) -> str:
    """Turn a text-label payload into a safe CSS class suffix.

    Keeps ASCII letters, digits, dash and underscore; replaces everything
    else with a dash, collapses runs, and trims leading/trailing dashes.
    Used for the ``text-<slug>`` group class when
    ``layer_per_feature=True``.
    """
    chars = [c if (c.isalnum() or c in "-_") else "-" for c in text.lower()]
    slug = "".join(chars).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "label"


def _rect(x: float, y: float, w: float, h: float, attrs: str) -> str:
    return f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}" {attrs}/>'


def _circle(cx: float, cy: float, r: float, attrs: str) -> str:
    return f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}" {attrs}/>'


def _style_attrs(fill: str, stroke: str | None) -> str:
    """Build the ``fill`` / ``stroke`` attribute pair for a shape.

    SVG's default stroke is ``none``, so a missing stroke is omitted.
    """
    parts = [f'fill="{escape(fill)}"']
    if stroke is not None:
        parts.append(f'stroke="{escape(stroke)}"')
    return " ".join(parts)


def svg_string(
    matrix: QrMatrix,
    params: GeometryParams,
    *,
    placement: QrPlacement | None = None,
    module_style: ModuleStyle = "square",
    text_labels: Sequence[TextLabel] = (),
    fill: str = "#000000",
    stroke: str | None = None,
    plate_fill: str | None = None,
    plate_stroke: str | None = None,
    layer_per_feature: bool = False,
) -> str:
    """Render the QR code + text labels as an SVG document string.

    The geometry math mirrors :func:`qr23mf.geometry.build_meshes` exactly
    so the 2D SVG and the 3D 3MF describe the same design in XY. The
    plate coordinate system is centered on ``(0, 0)`` in world space;
    this writer translates to an SVG-standard top-left origin and flips
    the Y axis so the result previews correctly in Inkscape / Illustrator
    / LightBurn.

    Args:
        matrix: QR module matrix from :func:`qr23mf.qr.build_matrix`.
        params: Plate dimensions.
        placement: Optional QR size + offset on the plate.
        module_style: ``"square"`` (default) or ``"dot"``.
        text_labels: Optional iterable of :class:`TextLabel` to rasterize.
        fill: Fill color applied to every QR module and text raster cell.
        stroke: Optional stroke color for the same shapes.
        plate_fill: When set, a plate-footprint ``<rect>`` is emitted
            underneath the features with this fill. Leave ``None`` to
            omit (the typical laser-etch use-case; the plate is a
            physical object, not part of the artwork).
        plate_stroke: Optional outline color for the plate rect.
        layer_per_feature: When ``True``, group the plate / QR modules /
            each text label in their own ``<g class="...">`` so laser
            software like LightBurn imports them as independent layers
            (cut / score / engrave) that can carry distinct power and
            speed settings. When ``False`` (default), everything lives
            in a single group.

    Returns:
        An SVG document as a string, ready to be written to disk.

    Raises:
        ValueError: On the same conditions as
            :func:`qr23mf.geometry.build_meshes`: unknown ``module_style``,
            per-module edge below :data:`MIN_MODULE_MM`, or a QR / text
            label that escapes the plate.
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

    plate_w = float(params.size_mm)
    plate_d = params.effective_depth_mm
    half_w = plate_w / 2.0
    half_d = plate_d / 2.0

    qr_footprint_mm = float(qr_size_mm_opt) if qr_size_mm_opt is not None else min(plate_w, plate_d)
    x_offset = float(placement.x_offset_mm) if placement is not None else 0.0
    y_offset = float(placement.y_offset_mm) if placement is not None else 0.0
    qr_half = qr_footprint_mm / 2.0

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
            f"{plate_w:g} x {plate_d:g} mm plate."
        )

    # World -> SVG coordinate mapping. World has plate centered on (0, 0)
    # with +Y up; SVG has the origin at top-left with +Y down.
    def to_svg(x_mm: float, y_mm: float) -> tuple[float, float]:
        return half_w + x_mm, half_d - y_mm

    quiet_offset_mm = params.quiet_zone_modules * module_mm
    qr_left_world = x_offset - qr_half + quiet_offset_mm
    qr_top_world = y_offset + qr_half - quiet_offset_mm

    feature_attrs = _style_attrs(fill, stroke)
    plate_attrs_str: str | None = None
    if plate_fill is not None or plate_stroke is not None:
        plate_attrs_str = _style_attrs(plate_fill or "none", plate_stroke)

    # --- Assemble SVG body --------------------------------------------------
    body: list[str] = []

    # Plate footprint (optional).
    if plate_attrs_str is not None:
        rect = _rect(0.0, 0.0, plate_w, plate_d, plate_attrs_str)
        if layer_per_feature:
            body.append(f'<g class="plate">{rect}</g>')
        else:
            body.append(rect)

    # QR modules.
    dark_rows, dark_cols = np.nonzero(matrix.modules)
    qr_shapes: list[str] = []
    if dark_rows.size > 0:
        if module_style == "square":
            for r, c in zip(dark_rows.tolist(), dark_cols.tolist(), strict=True):
                x0 = qr_left_world + c * module_mm
                y1 = qr_top_world - r * module_mm
                sx, sy = to_svg(x0, y1)
                qr_shapes.append(_rect(sx, sy, module_mm, module_mm, feature_attrs))
        else:  # "dot"
            radius = module_mm / 2.0
            for r, c in zip(dark_rows.tolist(), dark_cols.tolist(), strict=True):
                cx_world = qr_left_world + c * module_mm + radius
                cy_world = qr_top_world - r * module_mm - radius
                sx, sy = to_svg(cx_world, cy_world)
                qr_shapes.append(_circle(sx, sy, radius, feature_attrs))
    if qr_shapes:
        if layer_per_feature:
            body.append(f'<g class="qr">{"".join(qr_shapes)}</g>')
        else:
            body.append("".join(qr_shapes))

    # Text labels.
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
                f"{plate_w:g} x {plate_d:g} mm plate."
            )

        dark_r, dark_c = np.nonzero(grid)
        if dark_r.size == 0:
            continue

        text_shapes: list[str] = []
        for r, c in zip(dark_r.tolist(), dark_c.tolist(), strict=True):
            tx0_world = lx0 + c * cell_mm
            ty1_world = ly_top - r * cell_mm
            sx, sy = to_svg(tx0_world, ty1_world)
            text_shapes.append(_rect(sx, sy, cell_mm, cell_mm, feature_attrs))

        if layer_per_feature:
            body.append(f'<g class="text-{_slugify(label.content)}">{"".join(text_shapes)}</g>')
        else:
            body.append("".join(text_shapes))

    # --- Wrap in an <svg> root ---------------------------------------------
    header = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_fmt(plate_w)}mm" height="{_fmt(plate_d)}mm" '
        f'viewBox="0 0 {_fmt(plate_w)} {_fmt(plate_d)}">'
    )
    return header + "".join(body) + "</svg>"


def write_svg(
    matrix: QrMatrix,
    params: GeometryParams,
    path: Path,
    *,
    placement: QrPlacement | None = None,
    module_style: ModuleStyle = "square",
    text_labels: Sequence[TextLabel] = (),
    fill: str = "#000000",
    stroke: str | None = None,
    plate_fill: str | None = None,
    plate_stroke: str | None = None,
    layer_per_feature: bool = False,
) -> Path:
    """Render the design as an SVG and write it to ``path``.

    The ``.svg`` suffix is appended when missing, and parent directories
    are created. Returns the absolute path that was written so callers
    (CLI, GUI) can display it to the user.

    All keyword arguments are forwarded to :func:`svg_string`; see that
    function for semantics.
    """
    path = Path(path)
    if path.suffix.lower() != ".svg":
        path = path.with_suffix(".svg")
    path.parent.mkdir(parents=True, exist_ok=True)

    svg = svg_string(
        matrix,
        params,
        placement=placement,
        module_style=module_style,
        text_labels=text_labels,
        fill=fill,
        stroke=stroke,
        plate_fill=plate_fill,
        plate_stroke=plate_stroke,
        layer_per_feature=layer_per_feature,
    )
    path.write_text(svg, encoding="utf-8")
    return path
