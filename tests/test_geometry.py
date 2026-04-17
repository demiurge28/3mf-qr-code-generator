"""Tests for :mod:`qr23mf.geometry` (geometry-builder scope, FR-2/FR-4/NFR-1/NFR-2)."""

from __future__ import annotations

import numpy as np
import pytest
from stl.mesh import Mesh

from qr23mf.geometry import MIN_MODULE_MM, GeometryParams, build_meshes
from qr23mf.qr import QrMatrix, build_matrix

# --- GeometryParams validation -------------------------------------------------


def test_default_params_are_sensible() -> None:
    """Defaults match the acceptance criteria (60/2/1/4)."""
    p = GeometryParams()
    assert p.size_mm == 60.0
    assert p.base_height_mm == 2.0
    assert p.pixel_height_mm == 1.0
    assert p.quiet_zone_modules == 4


@pytest.mark.parametrize("bad", [0, -1, -0.5])
def test_non_positive_size_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="size_mm must be > 0"):
        GeometryParams(size_mm=bad)


@pytest.mark.parametrize("bad", [0, -0.01])
def test_non_positive_base_height_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="base_height_mm must be > 0"):
        GeometryParams(base_height_mm=bad)


@pytest.mark.parametrize("bad", [0, -2.5])
def test_non_positive_pixel_height_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="pixel_height_mm must be > 0"):
        GeometryParams(pixel_height_mm=bad)


def test_negative_quiet_zone_raises() -> None:
    with pytest.raises(ValueError, match="quiet_zone_modules must be >= 0"):
        GeometryParams(quiet_zone_modules=-1)


def test_non_int_quiet_zone_raises() -> None:
    with pytest.raises(ValueError, match="quiet_zone_modules must be an int"):
        GeometryParams(quiet_zone_modules=2.5)  # type: ignore[arg-type]


def test_bool_quiet_zone_raises() -> None:
    """``bool`` is an ``int`` subclass in Python; reject it explicitly."""
    with pytest.raises(ValueError, match="quiet_zone_modules must be an int"):
        GeometryParams(quiet_zone_modules=True)


def test_geometry_params_is_frozen() -> None:
    p = GeometryParams()
    with pytest.raises((AttributeError, TypeError)):
        p.size_mm = 10.0  # type: ignore[misc]


# --- build_meshes: shape / extent ---------------------------------------------


def _all_dark(size: int) -> QrMatrix:
    """Construct a synthetic QrMatrix with every module dark (for test clarity)."""
    return QrMatrix(
        modules=np.ones((size, size), dtype=np.bool_),
        size=size,
        version=1,
        ec="M",
    )


def _all_light(size: int) -> QrMatrix:
    return QrMatrix(
        modules=np.zeros((size, size), dtype=np.bool_),
        size=size,
        version=1,
        ec="M",
    )


def test_base_mesh_is_centered_axis_aligned_box() -> None:
    matrix = _all_light(21)
    params = GeometryParams(size_mm=60.0, base_height_mm=2.0)
    base, _ = build_meshes(matrix, params)

    assert isinstance(base, Mesh)
    # 6 faces x 2 triangles = 12
    assert base.vectors.shape == (12, 3, 3)
    verts = base.vectors.reshape(-1, 3)
    assert pytest.approx(verts[:, 0].min(), abs=1e-5) == -30.0
    assert pytest.approx(verts[:, 0].max(), abs=1e-5) == +30.0
    assert pytest.approx(verts[:, 1].min(), abs=1e-5) == -30.0
    assert pytest.approx(verts[:, 1].max(), abs=1e-5) == +30.0
    assert pytest.approx(verts[:, 2].min(), abs=1e-5) == 0.0
    assert pytest.approx(verts[:, 2].max(), abs=1e-5) == 2.0


def test_empty_matrix_produces_empty_pixels_mesh() -> None:
    matrix = _all_light(21)
    base, pixels = build_meshes(matrix, GeometryParams())
    assert base.vectors.shape == (12, 3, 3)
    assert pixels.vectors.shape == (0, 3, 3)


def test_pixel_count_matches_dark_module_count() -> None:
    matrix = build_matrix("https://example.com", ec="M")
    params = GeometryParams()
    _, pixels = build_meshes(matrix, params)
    expected_pixels = int(matrix.modules.sum())
    assert pixels.vectors.shape == (expected_pixels * 12, 3, 3)


def test_all_dark_produces_pixels_covering_usable_area() -> None:
    """With a fully-dark matrix, the pixel mesh XY extent equals the usable area."""
    size = 5
    params = GeometryParams(size_mm=100.0, quiet_zone_modules=2)
    matrix = _all_dark(size)
    _, pixels = build_meshes(matrix, params)

    module_mm = params.module_mm(size)
    quiet_mm = params.quiet_zone_modules * module_mm
    usable_half = 50.0 - quiet_mm

    verts = pixels.vectors.reshape(-1, 3)
    assert pytest.approx(verts[:, 0].min(), abs=1e-4) == -usable_half
    assert pytest.approx(verts[:, 0].max(), abs=1e-4) == +usable_half
    assert pytest.approx(verts[:, 1].min(), abs=1e-4) == -usable_half
    assert pytest.approx(verts[:, 1].max(), abs=1e-4) == +usable_half
    # Z should sit exactly on top of the base.
    assert pytest.approx(verts[:, 2].min(), abs=1e-4) == params.base_height_mm
    assert pytest.approx(verts[:, 2].max(), abs=1e-4) == (
        params.base_height_mm + params.pixel_height_mm
    )


# --- Orientation / normals ----------------------------------------------------


def test_base_mesh_normals_face_outward() -> None:
    """Each of the 12 triangles on the base box has a normal aligned with its face axis."""
    matrix = _all_light(21)
    base, _ = build_meshes(matrix, GeometryParams(size_mm=60.0, base_height_mm=2.0))
    # Base is centered on XY at z = [0, 2] -> box center at (0, 0, 1).
    box_center = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    for i in range(12):
        tri = base.vectors[i]
        # An axis-aligned face triangle has all 3 vertices sharing one coord.
        invariant_axis = next(
            (axis for axis in range(3) if np.allclose(tri[:, axis], tri[0, axis])),
            None,
        )
        assert invariant_axis is not None, f"triangle {i} is not axis-aligned: {tri}"
        face_coord = float(tri[0, invariant_axis])
        outward_sign = float(np.sign(face_coord - box_center[invariant_axis]))
        assert outward_sign != 0.0, f"triangle {i} sits at the box center on axis {invariant_axis}"
        normal = base.normals[i] / np.linalg.norm(base.normals[i])
        assert float(np.sign(normal[invariant_axis])) == outward_sign, (
            f"triangle {i}: normal {normal} does not face outward on axis "
            f"{invariant_axis} (face_coord={face_coord})"
        )


# --- Determinism / byte stability --------------------------------------------


def test_build_meshes_is_deterministic() -> None:
    matrix = build_matrix("qr23mf/determinism", ec="M")
    params = GeometryParams()
    a_base, a_px = build_meshes(matrix, params)
    b_base, b_px = build_meshes(matrix, params)
    assert np.array_equal(a_base.vectors, b_base.vectors)
    assert np.array_equal(a_px.vectors, b_px.vectors)


# --- "Size too small" guard ---------------------------------------------------


def test_size_too_small_raises_value_error() -> None:
    # 21-module matrix + 4 quiet zone on each side = 29 total modules.
    # 1 mm / 29 ≈ 0.0345 mm per module, which is below MIN_MODULE_MM.
    matrix = _all_light(21)
    with pytest.raises(ValueError, match="too small"):
        build_meshes(matrix, GeometryParams(size_mm=1.0))


def test_module_mm_convenience_matches_formula() -> None:
    p = GeometryParams(size_mm=60.0, quiet_zone_modules=4)
    assert p.module_mm(21) == pytest.approx(60.0 / 29.0)
    assert p.module_mm(25) == pytest.approx(60.0 / 33.0)


def test_min_module_mm_is_exposed() -> None:
    """The constant is part of the public surface (referenced in error messages)."""
    assert MIN_MODULE_MM > 0


# --- End-to-end with a real QR ------------------------------------------------


def test_real_qr_produces_reasonable_triangle_counts() -> None:
    matrix = build_matrix("https://github.com/demiurge28/3mf-qr-code-generator", ec="Q")
    base, pixels = build_meshes(matrix, GeometryParams())
    assert base.vectors.shape == (12, 3, 3)
    # Pixel count is 12 x number of dark modules.
    assert pixels.vectors.shape[0] % 12 == 0
    assert pixels.vectors.shape[0] > 0
