"""Tests for :mod:`qr23mf.geometry` (geometry-builder scope, FR-2/FR-4/NFR-1/NFR-2)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from stl.mesh import Mesh

from qr23mf.geometry import (
    MIN_MODULE_MM,
    GeometryParams,
    QrPlacement,
    TextLabel,
    build_meshes,
)
from qr23mf.qr import QrMatrix, build_matrix

# All-light / all-dark synthetic matrices are defined near the top of the
# existing test module; reuse via module-level helpers once defined below.

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
    """Triangle count is bounded by 12 per dark module.

    Adjacent dark modules share internal faces that the manifold-dedup
    post-process strips, so the real count is ``<= n_dark * 12`` and is
    always divisible by 4 (each shared face removes 4 triangles).
    """
    matrix = build_matrix("https://example.com", ec="M")
    params = GeometryParams()
    _, pixels = build_meshes(matrix, params)
    n_dark = int(matrix.modules.sum())
    assert pixels.vectors.shape[0] > 0
    assert pixels.vectors.shape[0] <= n_dark * 12
    assert pixels.vectors.shape[0] % 4 == 0


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
    # After the manifold dedup, counts are <= naive 12-per-module bound
    # and triangle pairs are dropped in fours (two back-to-back quads).
    n_dark = int(matrix.modules.sum())
    assert pixels.vectors.shape[0] > 0
    assert pixels.vectors.shape[0] <= n_dark * 12
    assert pixels.vectors.shape[0] % 4 == 0


# --- Non-square plate (depth_mm) ----------------------------------------------


@pytest.mark.parametrize("bad", [0, -0.01])
def test_non_positive_depth_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="depth_mm must be > 0"):
        GeometryParams(depth_mm=bad)


def test_effective_depth_defaults_to_size() -> None:
    """``depth_mm=None`` means square plate (effective depth == size_mm)."""
    p = GeometryParams(size_mm=80.0)
    assert p.effective_depth_mm == 80.0
    assert p.depth_mm is None


def test_rectangular_plate_produces_correct_base_extent() -> None:
    """A 100x50 mm plate must produce a base mesh with matching XY bounds."""
    matrix = _all_light(21)
    params = GeometryParams(size_mm=100.0, depth_mm=50.0, base_height_mm=2.0)
    base, _ = build_meshes(matrix, params)
    verts = base.vectors.reshape(-1, 3)
    assert pytest.approx(verts[:, 0].min(), abs=1e-5) == -50.0
    assert pytest.approx(verts[:, 0].max(), abs=1e-5) == +50.0
    assert pytest.approx(verts[:, 1].min(), abs=1e-5) == -25.0
    assert pytest.approx(verts[:, 1].max(), abs=1e-5) == +25.0


def test_rectangular_plate_centers_qr_on_smaller_dimension() -> None:
    """Without explicit placement, QR fills the smaller dimension (50 mm here)."""
    matrix = _all_dark(5)
    params = GeometryParams(size_mm=100.0, depth_mm=50.0, quiet_zone_modules=2)
    _, pixels = build_meshes(matrix, params)
    verts = pixels.vectors.reshape(-1, 3)
    module_mm = 50.0 / (5 + 2 * 2)
    usable_half = 50.0 / 2 - 2 * module_mm
    assert pytest.approx(verts[:, 0].min(), abs=1e-4) == -usable_half
    assert pytest.approx(verts[:, 0].max(), abs=1e-4) == +usable_half
    assert pytest.approx(verts[:, 1].min(), abs=1e-4) == -usable_half
    assert pytest.approx(verts[:, 1].max(), abs=1e-4) == +usable_half


# --- QrPlacement validation and positioning -----------------------------------


@pytest.mark.parametrize("bad", [0, -1.0])
def test_qr_placement_non_positive_size_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="qr_size_mm must be > 0"):
        QrPlacement(qr_size_mm=bad)


def test_qr_placement_defaults() -> None:
    p = QrPlacement()
    assert p.qr_size_mm is None
    assert p.x_offset_mm == 0.0
    assert p.y_offset_mm == 0.0


def test_qr_placement_shrinks_and_offsets_qr() -> None:
    """A 30 mm QR offset by +20 mm X must sit entirely right of plate center."""
    matrix = _all_dark(5)
    params = GeometryParams(size_mm=100.0, quiet_zone_modules=0)
    placement = QrPlacement(qr_size_mm=30.0, x_offset_mm=20.0)
    _, pixels = build_meshes(matrix, params, placement=placement)
    verts = pixels.vectors.reshape(-1, 3)
    # QR spans x in [20 - 15, 20 + 15] = [5, 35].
    assert pytest.approx(verts[:, 0].min(), abs=1e-4) == 5.0
    assert pytest.approx(verts[:, 0].max(), abs=1e-4) == 35.0
    assert pytest.approx(verts[:, 1].min(), abs=1e-4) == -15.0
    assert pytest.approx(verts[:, 1].max(), abs=1e-4) == +15.0


def test_qr_placement_outside_plate_raises() -> None:
    matrix = _all_dark(5)
    params = GeometryParams(size_mm=40.0, quiet_zone_modules=0)
    placement = QrPlacement(qr_size_mm=30.0, x_offset_mm=15.0)  # reaches x=30 > 20
    with pytest.raises(ValueError, match="extends outside"):
        build_meshes(matrix, params, placement=placement)


# --- Module style ------------------------------------------------------------


def test_dot_style_triangle_count_matches_expected() -> None:
    """Each dark module in 'dot' mode is a 16-gon prism = 4*16-4 = 60 triangles."""
    matrix = _all_dark(5)
    params = GeometryParams(size_mm=50.0, quiet_zone_modules=0)
    _, pixels = build_meshes(matrix, params, module_style="dot")
    n_dark = int(matrix.modules.sum())
    assert pixels.vectors.shape == (n_dark * 60, 3, 3)


def test_dot_style_prism_radius_matches_module() -> None:
    matrix = _all_dark(3)
    params = GeometryParams(size_mm=30.0, quiet_zone_modules=0)
    _, pixels = build_meshes(matrix, params, module_style="dot")
    verts = pixels.vectors.reshape(-1, 3)
    # A regular 16-gon inscribed in a circle of radius 5 has its furthest
    # vertex at r * cos(pi/16) ~= 4.904 from the center in any cardinal
    # direction (because the polygon is rotated by pi/sides for aesthetics).
    # With the right-most dot center at +10, max x is therefore ~14.904.
    module_mm = 30.0 / 3
    radius = module_mm / 2.0
    expected_max = 10.0 + radius * math.cos(math.pi / 16)
    assert pytest.approx(verts[:, 0].max(), rel=1e-3) == expected_max
    assert pytest.approx(verts[:, 0].min(), rel=1e-3) == -expected_max
    # And the 16-gon is fully contained within the square module footprint.
    assert verts[:, 0].max() < 15.0 + 1e-4
    assert verts[:, 0].min() > -15.0 - 1e-4


def test_invalid_module_style_raises() -> None:
    matrix = _all_light(21)
    with pytest.raises(ValueError, match="module_style must be one of"):
        build_meshes(matrix, GeometryParams(), module_style="triangle")  # type: ignore[arg-type]


def test_default_style_is_square_and_backwards_compatible() -> None:
    matrix = build_matrix("back-compat", ec="M")
    params = GeometryParams()
    _, before = build_meshes(matrix, params)
    _, after = build_meshes(matrix, params, module_style="square")
    assert np.array_equal(before.vectors, after.vectors)


# --- TextLabel ----------------------------------------------------------------


def test_text_label_rejects_empty_content() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        TextLabel(content="", x_mm=0, y_mm=0, height_mm=5, extrusion_mm=1)


def test_text_label_rejects_non_positive_height() -> None:
    with pytest.raises(ValueError, match="height_mm must be > 0"):
        TextLabel(content="hi", x_mm=0, y_mm=0, height_mm=0, extrusion_mm=1)


def test_text_label_rejects_non_positive_extrusion() -> None:
    with pytest.raises(ValueError, match="extrusion_mm must be > 0"):
        TextLabel(content="hi", x_mm=0, y_mm=0, height_mm=5, extrusion_mm=0)


def test_text_labels_contribute_triangles_to_features_mesh() -> None:
    """A text label with visible content must add triangles on top of the QR."""
    matrix = _all_light(5)  # zero QR dark modules
    params = GeometryParams(size_mm=80.0, quiet_zone_modules=0)
    label = TextLabel(content="A", x_mm=0.0, y_mm=0.0, height_mm=10.0, extrusion_mm=1.0)
    _, features = build_meshes(matrix, params, text_labels=(label,))
    # Each dark raster pixel contributes up to 12 triangles; the manifold
    # dedup drops shared internal faces, so triangle counts come in
    # multiples of 4 after pruning.
    assert features.vectors.shape[0] > 0
    assert features.vectors.shape[0] % 4 == 0


def test_text_labels_above_plate_top() -> None:
    """Text extrusion must sit strictly above the plate top face."""
    matrix = _all_light(5)
    params = GeometryParams(size_mm=80.0, base_height_mm=2.0, quiet_zone_modules=0)
    label = TextLabel(content="Z", x_mm=0.0, y_mm=0.0, height_mm=10.0, extrusion_mm=1.5)
    _, features = build_meshes(matrix, params, text_labels=(label,))
    verts = features.vectors.reshape(-1, 3)
    assert pytest.approx(verts[:, 2].min(), abs=1e-4) == params.base_height_mm
    assert pytest.approx(verts[:, 2].max(), abs=1e-4) == (
        params.base_height_mm + label.extrusion_mm
    )


def test_text_label_outside_plate_raises() -> None:
    matrix = _all_light(5)
    params = GeometryParams(size_mm=20.0, quiet_zone_modules=0)
    # 'MMM' is wide enough to exceed a 20 mm plate when height_mm=15.
    label = TextLabel(content="MMM", x_mm=0.0, y_mm=0.0, height_mm=15.0, extrusion_mm=1.0)
    with pytest.raises(ValueError, match="extends outside"):
        build_meshes(matrix, params, text_labels=(label,))


def test_build_meshes_default_extras_produce_byte_identical_output() -> None:
    """Passing placement=None, module_style='square', text_labels=() is a no-op."""
    matrix = build_matrix("extras-are-noop", ec="M")
    params = GeometryParams()
    a_base, a_feats = build_meshes(matrix, params)
    b_base, b_feats = build_meshes(
        matrix, params, placement=None, module_style="square", text_labels=()
    )
    assert np.array_equal(a_base.vectors, b_base.vectors)
    assert np.array_equal(a_feats.vectors, b_feats.vectors)


# --- QR finish: extruded / flush / sunken -------------------------------------


def test_invalid_qr_finish_raises() -> None:
    matrix = _all_light(21)
    with pytest.raises(ValueError, match="qr_finish must be one of"):
        build_meshes(matrix, GeometryParams(), qr_finish="debossed")  # type: ignore[arg-type]


def test_default_qr_finish_matches_extruded() -> None:
    matrix = build_matrix("finish-compat", ec="M")
    params = GeometryParams()
    a_base, a_feats = build_meshes(matrix, params)
    b_base, b_feats = build_meshes(matrix, params, qr_finish="extruded")
    assert np.array_equal(a_base.vectors, b_base.vectors)
    assert np.array_equal(a_feats.vectors, b_feats.vectors)


def test_flush_pixels_occupy_top_slab_of_base() -> None:
    """Flush pixels must sit between base_h - pixel_h and base_h."""
    matrix = _all_dark(3)
    params = GeometryParams(
        size_mm=30.0, base_height_mm=2.0, pixel_height_mm=1.0, quiet_zone_modules=0
    )
    _, pixels = build_meshes(matrix, params, qr_finish="flush")
    verts = pixels.vectors.reshape(-1, 3)
    assert pytest.approx(verts[:, 2].min(), abs=1e-4) == 1.0
    assert pytest.approx(verts[:, 2].max(), abs=1e-4) == 2.0


def test_flush_leaves_base_mesh_unchanged() -> None:
    matrix = _all_dark(3)
    params = GeometryParams(
        size_mm=30.0, base_height_mm=2.0, pixel_height_mm=1.0, quiet_zone_modules=0
    )
    base_extruded, _ = build_meshes(matrix, params, qr_finish="extruded")
    base_flush, _ = build_meshes(matrix, params, qr_finish="flush")
    assert np.array_equal(base_extruded.vectors, base_flush.vectors)


def test_flush_rejects_pixel_height_not_less_than_base() -> None:
    matrix = _all_dark(3)
    # pixel_height == base_height: flush would zero out the base.
    params = GeometryParams(
        size_mm=30.0, base_height_mm=2.0, pixel_height_mm=2.0, quiet_zone_modules=0
    )
    with pytest.raises(ValueError, match="pixel_height_mm"):
        build_meshes(matrix, params, qr_finish="flush")


def test_sunken_base_contains_pocket_geometry() -> None:
    """Sunken base must have more triangles than a plain box when any cell is light.

    A realistic QR has both dark and light cells, so the base mesh is
    composed of a bottom slab plus per-light-cell top boxes plus any
    margin strips.
    """
    matrix = build_matrix("sunken-base-has-pockets", ec="M")
    params = GeometryParams(
        size_mm=60.0, base_height_mm=2.0, pixel_height_mm=1.0, quiet_zone_modules=2
    )
    base, pixels = build_meshes(matrix, params, qr_finish="sunken")
    # Bottom slab (12) + quiet-zone ring + light-cell boxes must exceed 12.
    # After the manifold dedup, shared faces are removed so total count is
    # no longer a multiple of 12 — just assert it's a multiple of 4 and
    # above the plain-box threshold.
    assert base.vectors.shape[0] > 12
    assert base.vectors.shape[0] % 4 == 0
    # Pixels still fill the top slab (same z range as flush).
    verts = pixels.vectors.reshape(-1, 3)
    assert pytest.approx(verts[:, 2].min(), abs=1e-4) == 1.0
    assert pytest.approx(verts[:, 2].max(), abs=1e-4) == 2.0


def test_sunken_plate_bottom_face_reaches_z_zero() -> None:
    """Sunken base spans z in [0, base_h]; min is 0, max is base_h.

    Use a realistic matrix with both light and dark cells so the top layer
    has both pocketed (dark) and filled (light) regions.
    """
    matrix = build_matrix("sunken-bottom-z", ec="M")
    params = GeometryParams(
        size_mm=60.0, base_height_mm=2.0, pixel_height_mm=1.0, quiet_zone_modules=2
    )
    base, _ = build_meshes(matrix, params, qr_finish="sunken")
    verts = base.vectors.reshape(-1, 3)
    assert pytest.approx(verts[:, 2].min(), abs=1e-4) == 0.0
    assert pytest.approx(verts[:, 2].max(), abs=1e-4) == 2.0


def test_sunken_all_light_matrix_emits_full_plate() -> None:
    """With zero dark modules the sunken base should cover the full plate top."""
    matrix = _all_light(5)
    params = GeometryParams(
        size_mm=30.0, base_height_mm=2.0, pixel_height_mm=1.0, quiet_zone_modules=0
    )
    base, pixels = build_meshes(matrix, params, qr_finish="sunken")
    # Features mesh is empty (no dark modules).
    assert pixels.vectors.shape == (0, 3, 3)
    # Base covers full plate footprint (min/max XY = +/- half, min/max Z = 0/base_h).
    verts = base.vectors.reshape(-1, 3)
    assert pytest.approx(verts[:, 0].min(), abs=1e-4) == -15.0
    assert pytest.approx(verts[:, 0].max(), abs=1e-4) == +15.0
    assert pytest.approx(verts[:, 1].min(), abs=1e-4) == -15.0
    assert pytest.approx(verts[:, 1].max(), abs=1e-4) == +15.0
    assert pytest.approx(verts[:, 2].min(), abs=1e-4) == 0.0
    assert pytest.approx(verts[:, 2].max(), abs=1e-4) == 2.0


def test_sunken_rejects_pixel_height_not_less_than_base() -> None:
    matrix = _all_dark(3)
    params = GeometryParams(
        size_mm=30.0, base_height_mm=1.0, pixel_height_mm=1.0, quiet_zone_modules=0
    )
    with pytest.raises(ValueError, match="pixel_height_mm"):
        build_meshes(matrix, params, qr_finish="sunken")


# --- Manifold output (no back-to-back internal faces) -------------------------


def _count_duplicate_vertex_sets(mesh_vectors: np.ndarray) -> int:
    """Count triangles whose canonical sorted-vertex key is shared by another.

    Back-to-back internal faces (the non-manifold signature Bambu Studio /
    OrcaSlicer flag) show up as at least two triangles with identical
    vertex sets regardless of winding.
    """
    seen: dict[bytes, int] = {}
    for i in range(mesh_vectors.shape[0]):
        verts = mesh_vectors[i]
        idx = np.lexsort(verts.T[::-1])
        key = verts[idx].tobytes()
        seen[key] = seen.get(key, 0) + 1
    return sum(count for count in seen.values() if count > 1)


def test_features_mesh_has_no_back_to_back_internal_faces() -> None:
    """Adjacent dark QR modules must not emit coincident shared faces.

    A 2x2-module all-dark region produces 4 touching cubes. Without
    dedup, the shared inner faces add 8 coincident triangles; with
    dedup, every surviving triangle's sorted-vertex key is unique.
    """
    matrix = _all_dark(2)
    params = GeometryParams(size_mm=20.0, quiet_zone_modules=0)
    _, features = build_meshes(matrix, params)
    assert features.vectors.shape[0] > 0
    assert _count_duplicate_vertex_sets(features.vectors) == 0


def test_sunken_base_mesh_has_no_back_to_back_internal_faces() -> None:
    """Sunken base is a composite of touching boxes; dedup must remove shared faces."""
    matrix = build_matrix("sunken-manifold", ec="M")
    params = GeometryParams(
        size_mm=60.0, base_height_mm=2.0, pixel_height_mm=1.0, quiet_zone_modules=2
    )
    base, _ = build_meshes(matrix, params, qr_finish="sunken")
    assert _count_duplicate_vertex_sets(base.vectors) == 0


def test_real_qr_square_mode_features_mesh_is_manifold_shaped() -> None:
    """End-to-end check on a realistic QR: no coincident internal faces."""
    matrix = build_matrix("https://example.com/manifold-check", ec="M")
    params = GeometryParams()
    _, features = build_meshes(matrix, params, module_style="square")
    assert _count_duplicate_vertex_sets(features.vectors) == 0
