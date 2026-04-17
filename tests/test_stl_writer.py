"""Tests for :mod:`qr23mf.writers.stl` (bootstrap primitive)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from stl.mesh import Mesh

from qr23mf.geometry import GeometryParams, build_meshes
from qr23mf.qr import QrMatrix, build_matrix
from qr23mf.writers.stl import write_stl


def _empty_matrix() -> QrMatrix:
    return QrMatrix(
        modules=np.zeros((5, 5), dtype=np.bool_),
        size=5,
        version=1,
        ec="M",
    )


def test_write_stl_produces_loadable_binary_file(tmp_path: Path) -> None:
    matrix = build_matrix("qr23mf/writer/smoke", ec="M")
    base, pixels = build_meshes(matrix, GeometryParams())
    out = tmp_path / "out.stl"
    write_stl(base, pixels, out)
    loaded = Mesh.from_file(str(out))
    expected_tris = base.vectors.shape[0] + pixels.vectors.shape[0]
    assert loaded.vectors.shape == (expected_tris, 3, 3)


def test_write_stl_with_empty_pixels_still_writes_base(tmp_path: Path) -> None:
    base, pixels = build_meshes(_empty_matrix(), GeometryParams(size_mm=50))
    out = tmp_path / "base_only.stl"
    write_stl(base, pixels, out)
    loaded = Mesh.from_file(str(out))
    assert loaded.vectors.shape == (12, 3, 3)


def test_write_stl_creates_parent_directories(tmp_path: Path) -> None:
    matrix = build_matrix("hi", ec="L")
    base, pixels = build_meshes(matrix, GeometryParams())
    out = tmp_path / "nested" / "dir" / "out.stl"
    write_stl(base, pixels, out)
    assert out.exists()


def test_write_stl_produces_deterministic_triangle_payload(tmp_path: Path) -> None:
    """numpy-stl stamps the 80-byte header with a timestamp; the triangle
    payload (bytes 80+) must still match across runs.

    The formal ``stl-writer`` scope will add byte-level determinism across
    the entire file including the header.
    """
    matrix = build_matrix("deterministic", ec="M")
    base, pixels = build_meshes(matrix, GeometryParams())
    a = tmp_path / "a.stl"
    b = tmp_path / "b.stl"
    write_stl(base, pixels, a)
    write_stl(base, pixels, b)
    assert a.read_bytes()[80:] == b.read_bytes()[80:]
