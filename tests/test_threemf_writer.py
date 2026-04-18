"""Tests for :mod:`qr23mf.writers.threemf` (two-object 3MF writer)."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from qr23mf.geometry import GeometryParams, build_meshes
from qr23mf.qr import QrMatrix, build_matrix
from qr23mf.writers.threemf import write_3mf

_CORE_NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def _empty_matrix() -> QrMatrix:
    return QrMatrix(
        modules=np.zeros((5, 5), dtype=np.bool_),
        size=5,
        version=1,
        ec="M",
    )


def _parse_model(archive: Path) -> ET.Element:
    with zipfile.ZipFile(archive) as zf, zf.open("3D/3dmodel.model") as fh:
        return ET.parse(fh).getroot()


# --- Package structure --------------------------------------------------------


def test_write_3mf_produces_valid_zip_with_required_parts(tmp_path: Path) -> None:
    matrix = build_matrix("qr23mf/3mf/smoke", ec="M")
    base, features = build_meshes(matrix, GeometryParams())
    out = tmp_path / "plate.3mf"
    written = write_3mf(base, features, out)
    assert written == out
    assert out.exists()
    assert zipfile.is_zipfile(out)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert names == {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}


def test_write_3mf_appends_suffix_when_missing(tmp_path: Path) -> None:
    matrix = build_matrix("hi", ec="L")
    base, features = build_meshes(matrix, GeometryParams())
    out_without_suffix = tmp_path / "plate"
    written = write_3mf(base, features, out_without_suffix)
    assert written == tmp_path / "plate.3mf"
    assert written.exists()


def test_write_3mf_creates_parent_directories(tmp_path: Path) -> None:
    matrix = build_matrix("hi", ec="L")
    base, features = build_meshes(matrix, GeometryParams())
    out = tmp_path / "nested" / "dir" / "plate.3mf"
    write_3mf(base, features, out)
    assert out.exists()


# --- Model XML content --------------------------------------------------------


def test_write_3mf_emits_two_objects_when_features_non_empty(tmp_path: Path) -> None:
    matrix = build_matrix("two-objects", ec="M")
    base, features = build_meshes(matrix, GeometryParams())
    assert features.vectors.shape[0] > 0  # sanity
    out = tmp_path / "plate.3mf"
    write_3mf(base, features, out)
    root = _parse_model(out)
    objects = root.findall(f"{_CORE_NS}resources/{_CORE_NS}object")
    items = root.findall(f"{_CORE_NS}build/{_CORE_NS}item")
    assert len(objects) == 2
    assert {o.get("id") for o in objects} == {"1", "2"}
    assert {o.get("type") for o in objects} == {"model"}
    assert {i.get("objectid") for i in items} == {"1", "2"}


def test_write_3mf_omits_features_object_when_mesh_empty(tmp_path: Path) -> None:
    base, features = build_meshes(_empty_matrix(), GeometryParams(size_mm=50))
    assert features.vectors.shape[0] == 0  # sanity
    out = tmp_path / "base-only.3mf"
    write_3mf(base, features, out)
    root = _parse_model(out)
    objects = root.findall(f"{_CORE_NS}resources/{_CORE_NS}object")
    items = root.findall(f"{_CORE_NS}build/{_CORE_NS}item")
    assert len(objects) == 1
    assert objects[0].get("id") == "1"
    assert [i.get("objectid") for i in items] == ["1"]


def test_model_vertex_and_triangle_counts_match_mesh(tmp_path: Path) -> None:
    """Triangle counts match; vertex counts reflect position-level dedup.

    The writer deduplicates vertices by exact position so slicers see
    shared edges and treat the mesh as manifold. A simple axis-aligned
    box (the base) collapses from 36 vertex entries to exactly 8 corner
    vertices; the features mesh still has at most ``3 * n_triangles``
    vertex entries (and usually far fewer once adjacent cells are merged).
    """
    matrix = build_matrix("counts", ec="M")
    base, features = build_meshes(matrix, GeometryParams())
    n_base = base.vectors.shape[0]
    n_feat = features.vectors.shape[0]
    out = tmp_path / "plate.3mf"
    write_3mf(base, features, out)
    root = _parse_model(out)
    obj1, obj2 = root.findall(f"{_CORE_NS}resources/{_CORE_NS}object")
    mesh1_vertices = obj1.findall(f"{_CORE_NS}mesh/{_CORE_NS}vertices/{_CORE_NS}vertex")
    mesh1_triangles = obj1.findall(f"{_CORE_NS}mesh/{_CORE_NS}triangles/{_CORE_NS}triangle")
    mesh2_vertices = obj2.findall(f"{_CORE_NS}mesh/{_CORE_NS}vertices/{_CORE_NS}vertex")
    mesh2_triangles = obj2.findall(f"{_CORE_NS}mesh/{_CORE_NS}triangles/{_CORE_NS}triangle")
    assert len(mesh1_triangles) == n_base
    # Base is a simple box: exactly 8 unique corner vertices.
    assert len(mesh1_vertices) == 8
    assert len(mesh2_triangles) == n_feat
    assert 0 < len(mesh2_vertices) <= n_feat * 3


def test_model_declares_millimeter_units(tmp_path: Path) -> None:
    matrix = build_matrix("units", ec="L")
    base, features = build_meshes(matrix, GeometryParams())
    out = tmp_path / "plate.3mf"
    write_3mf(base, features, out)
    root = _parse_model(out)
    assert root.get("unit") == "millimeter"


def test_write_3mf_is_byte_identical_across_runs(tmp_path: Path) -> None:
    """Fixed ZIP timestamps + deterministic XML make repeat writes identical."""
    matrix = build_matrix("deterministic-3mf", ec="M")
    base, features = build_meshes(matrix, GeometryParams())
    a = tmp_path / "a.3mf"
    b = tmp_path / "b.3mf"
    write_3mf(base, features, a)
    write_3mf(base, features, b)
    assert a.read_bytes() == b.read_bytes()


def test_emitted_mesh_is_edge_manifold(tmp_path: Path) -> None:
    """Every edge in each emitted ``<object>`` is shared by exactly 2 triangles.

    Slicers (Bambu Studio, OrcaSlicer, PrusaSlicer) walk edges by vertex
    index; an edge touched by only one triangle or by three or more shows
    up as 'non-manifold'. This test parses each object's triangles as
    (sorted) index pairs and asserts every pair has count == 2.
    """
    matrix = build_matrix("https://example.com/manifold-3mf", ec="M")
    base, features = build_meshes(matrix, GeometryParams())
    out = tmp_path / "plate.3mf"
    write_3mf(base, features, out)
    root = _parse_model(out)
    objects = root.findall(f"{_CORE_NS}resources/{_CORE_NS}object")
    assert objects, "at least one object expected"
    for obj in objects:
        edges: dict[tuple[int, int], int] = {}
        for t in obj.findall(f"{_CORE_NS}mesh/{_CORE_NS}triangles/{_CORE_NS}triangle"):
            v1 = int(t.get("v1") or 0)
            v2 = int(t.get("v2") or 0)
            v3 = int(t.get("v3") or 0)
            for a, b in ((v1, v2), (v2, v3), (v3, v1)):
                key = (a, b) if a < b else (b, a)
                edges[key] = edges.get(key, 0) + 1
        bad = {edge: count for edge, count in edges.items() if count != 2}
        assert not bad, (
            f"object id={obj.get('id')} has {len(bad)} non-manifold edges "
            f"(expected each edge touched by exactly 2 triangles)."
        )
