"""Tests for :mod:`qr23mf.writers.svg` (SVG writer).

Covers the acceptance criteria from issue #3:

* Valid XML + well-formed ``<svg>`` root with ``viewBox`` / ``width`` /
  ``height`` in millimetres.
* Dark-module count matches ``matrix.modules.sum()``.
* Dot style emits ``<circle>`` elements instead of ``<rect>``.
* Text labels contribute additional ``<rect>``s per rasterized dark cell.
* Plate footprint is emitted iff ``plate_fill`` / ``plate_stroke`` is set.
* ``layer_per_feature`` produces named ``<g class=...>`` groups.
* ``write_svg`` appends the ``.svg`` suffix when missing and creates
  parent directories.
* Unknown ``module_style`` is rejected with ``ValueError``.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from qr23mf.geometry import GeometryParams, QrPlacement, TextLabel
from qr23mf.qr import QrMatrix, build_matrix
from qr23mf.writers.svg import svg_string, write_svg

_SVG_NS = "{http://www.w3.org/2000/svg}"


def _all_light(size: int) -> QrMatrix:
    return QrMatrix(modules=np.zeros((size, size), dtype=np.bool_), size=size, version=1, ec="M")


def _all_dark(size: int) -> QrMatrix:
    return QrMatrix(modules=np.ones((size, size), dtype=np.bool_), size=size, version=1, ec="M")


def _parse(svg: str) -> ET.Element:
    return ET.fromstring(svg)


# --- Root / viewBox / units ---------------------------------------------------


def test_svg_root_has_mm_width_height_and_matching_viewbox() -> None:
    matrix = build_matrix("viewbox", ec="M")
    svg = svg_string(matrix, GeometryParams(size_mm=60.0, depth_mm=80.0))
    root = _parse(svg)
    assert root.tag == f"{_SVG_NS}svg"
    assert root.get("width") == "60mm"
    assert root.get("height") == "80mm"
    assert root.get("viewBox") == "0 0 60 80"


def test_svg_is_valid_xml_with_xml_declaration() -> None:
    matrix = build_matrix("valid-xml", ec="M")
    svg = svg_string(matrix, GeometryParams())
    assert svg.startswith('<?xml version="1.0" encoding="UTF-8"?>\n')
    # ElementTree.fromstring would raise ParseError if it weren't well-formed.
    _parse(svg)


# --- Shape counts -------------------------------------------------------------


def test_square_style_emits_one_rect_per_dark_module() -> None:
    matrix = build_matrix("square-count", ec="M")
    svg = svg_string(matrix, GeometryParams(size_mm=60.0), module_style="square")
    root = _parse(svg)
    rects = root.findall(f".//{_SVG_NS}rect")
    assert len(rects) == int(matrix.modules.sum())


def test_dot_style_emits_one_circle_per_dark_module_and_no_qr_rects() -> None:
    matrix = build_matrix("dot-count", ec="M")
    svg = svg_string(matrix, GeometryParams(size_mm=60.0), module_style="dot")
    root = _parse(svg)
    circles = root.findall(f".//{_SVG_NS}circle")
    assert len(circles) == int(matrix.modules.sum())
    # No <rect> when no plate fill and no text labels.
    assert root.findall(f".//{_SVG_NS}rect") == []


# --- Module sizing ------------------------------------------------------------


def test_each_module_is_plate_over_total_modules() -> None:
    """For an N-module QR with zero quiet zone, each square is plate / N mm."""
    matrix = _all_dark(5)
    params = GeometryParams(size_mm=30.0, quiet_zone_modules=0)
    svg = svg_string(matrix, params, module_style="square")
    root = _parse(svg)
    rects = root.findall(f".//{_SVG_NS}rect")
    assert rects, "expected at least one rect"
    # Each rect should be 30 / 5 = 6 mm on a side.
    assert float(rects[0].get("width") or 0) == pytest.approx(6.0, abs=1e-6)
    assert float(rects[0].get("height") or 0) == pytest.approx(6.0, abs=1e-6)


# --- Plate footprint ----------------------------------------------------------


def test_plate_fill_emits_full_plate_rect_before_modules() -> None:
    matrix = build_matrix("plate-fill", ec="M")
    svg = svg_string(matrix, GeometryParams(size_mm=60.0), plate_fill="white")
    root = _parse(svg)
    rects = root.findall(f".//{_SVG_NS}rect")
    assert rects, "expected at least one rect"
    first = rects[0]
    assert first.get("x") == "0"
    assert first.get("y") == "0"
    assert first.get("width") == "60"
    assert first.get("height") == "60"
    assert first.get("fill") == "white"


def test_no_plate_fill_by_default() -> None:
    matrix = build_matrix("no-plate", ec="M")
    svg = svg_string(matrix, GeometryParams(size_mm=60.0))
    root = _parse(svg)
    # Only QR module rects should be present; each is smaller than the plate.
    for r in root.findall(f".//{_SVG_NS}rect"):
        assert float(r.get("width") or 0) < 60.0


# --- layer_per_feature --------------------------------------------------------


def test_layer_per_feature_wraps_plate_qr_and_labels_in_named_groups() -> None:
    matrix = build_matrix("layers", ec="M")
    label = TextLabel(content="A", x_mm=0.0, y_mm=-20.0, height_mm=6.0, extrusion_mm=0.5)
    svg = svg_string(
        matrix,
        GeometryParams(size_mm=60.0, depth_mm=80.0),
        text_labels=(label,),
        plate_fill="white",
        layer_per_feature=True,
    )
    root = _parse(svg)
    groups = root.findall(f"{_SVG_NS}g")
    classes = [g.get("class") for g in groups]
    assert "plate" in classes
    assert "qr" in classes
    assert any(c and c.startswith("text-") for c in classes)


# --- Text labels --------------------------------------------------------------


def test_text_label_adds_raster_rects() -> None:
    matrix = _all_light(5)  # no QR modules
    label = TextLabel(content="A", x_mm=0.0, y_mm=0.0, height_mm=10.0, extrusion_mm=1.0)
    svg = svg_string(
        matrix,
        GeometryParams(size_mm=80.0, quiet_zone_modules=0),
        text_labels=(label,),
    )
    root = _parse(svg)
    rects = root.findall(f".//{_SVG_NS}rect")
    # No QR (all-light), so every rect comes from the text raster.
    assert len(rects) > 0


# --- Placement ---------------------------------------------------------------


def test_qr_placement_shifts_first_module_into_svg_space() -> None:
    """First module (row 0, col 0) of a 30 mm QR at +20 mm X should land at SVG (55, 35)."""
    matrix = _all_dark(3)
    params = GeometryParams(size_mm=100.0, quiet_zone_modules=0)
    placement = QrPlacement(qr_size_mm=30.0, x_offset_mm=20.0)
    svg = svg_string(matrix, params, placement=placement)
    root = _parse(svg)
    rects = root.findall(f".//{_SVG_NS}rect")
    assert rects, "expected at least one rect"
    first = rects[0]
    # World: first dark module is at (x=5, y=15) relative to plate center.
    # SVG: plate 100x100, origin top-left, Y flipped -> (50+5, 50-15) = (55, 35).
    assert float(first.get("x") or 0) == pytest.approx(55.0, abs=1e-6)
    assert float(first.get("y") or 0) == pytest.approx(35.0, abs=1e-6)
    assert float(first.get("width") or 0) == pytest.approx(10.0, abs=1e-6)


def test_qr_placement_outside_plate_raises() -> None:
    matrix = _all_dark(3)
    params = GeometryParams(size_mm=40.0, quiet_zone_modules=0)
    placement = QrPlacement(qr_size_mm=30.0, x_offset_mm=15.0)
    with pytest.raises(ValueError, match="extends outside"):
        svg_string(matrix, params, placement=placement)


# --- Error paths -------------------------------------------------------------


def test_unknown_module_style_raises() -> None:
    matrix = _all_light(21)
    with pytest.raises(ValueError, match="module_style must be one of"):
        svg_string(matrix, GeometryParams(), module_style="triangle")  # type: ignore[arg-type]


def test_size_too_small_raises() -> None:
    matrix = _all_light(21)
    with pytest.raises(ValueError, match="too small"):
        svg_string(matrix, GeometryParams(size_mm=1.0))


# --- write_svg file output ---------------------------------------------------


def test_write_svg_appends_suffix_when_missing(tmp_path: Path) -> None:
    matrix = build_matrix("suffix", ec="L")
    out = tmp_path / "coaster"  # no suffix
    written = write_svg(matrix, GeometryParams(), out)
    assert written == tmp_path / "coaster.svg"
    assert written.exists()
    root = _parse(written.read_text(encoding="utf-8"))
    assert root.tag == f"{_SVG_NS}svg"


def test_write_svg_creates_parent_directories(tmp_path: Path) -> None:
    matrix = build_matrix("parent", ec="L")
    out = tmp_path / "nested" / "dir" / "coaster.svg"
    written = write_svg(matrix, GeometryParams(), out)
    assert written.exists()


def test_write_svg_returns_path_with_svg_suffix(tmp_path: Path) -> None:
    matrix = build_matrix("path", ec="L")
    out = tmp_path / "plate.svg"
    written = write_svg(matrix, GeometryParams(), out)
    assert written.suffix.lower() == ".svg"
    assert written == out
