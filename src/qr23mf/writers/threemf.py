"""Two-object 3MF writer.

A 3MF package is a ZIP archive containing three parts:

* ``[Content_Types].xml`` \u2014 declares MIME types for the package's parts.
* ``_rels/.rels``          \u2014 root relationships pointing at the model part.
* ``3D/3dmodel.model``     \u2014 the model XML with ``<resources>`` listing
  one or more ``<object>`` elements and a ``<build>`` section with one
  ``<item>`` per object to include in the printable scene.

This writer emits **two** ``<object>`` entries \u2014 one for the base plate
(``objectid=1``) and one for the QR / text feature mesh (``objectid=2``).
Slicers like Bambu Studio, OrcaSlicer, and PrusaSlicer import these as
two independently selectable bodies, so the user can assign a different
filament to each for a two-color print. When the features mesh is empty
the second object is omitted and the package contains a single body.

The writer is pure-stdlib (``zipfile`` + string XML construction) and
deterministic: all ZIP entries are stamped with a fixed 1980-01-01
timestamp, so repeat writes of the same mesh produce byte-identical
output.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

from stl.mesh import Mesh

__all__ = ["write_3mf"]

_CORE_NS: str = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_TYPES_NS: str = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELS_NS: str = "http://schemas.openxmlformats.org/package/2006/relationships"
_MODEL_REL_TYPE: str = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"

_CONTENT_TYPES_XML: str = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<Types xmlns="{_TYPES_NS}">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" '
    'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)

_RELS_XML: str = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<Relationships xmlns="{_RELS_NS}">'
    f'<Relationship Id="rel0" Type="{_MODEL_REL_TYPE}" Target="/3D/3dmodel.model"/>'
    "</Relationships>"
)

# Fixed ZIP timestamp so repeat writes are byte-identical on the same
# platform. 1980-01-01 is the minimum ZIP can represent.
_FIXED_ZIP_DATE: tuple[int, int, int, int, int, int] = (1980, 1, 1, 0, 0, 0)


def _object_xml(object_id: int, mesh: Mesh) -> str:
    """Serialize a mesh to a 3MF ``<object>`` element.

    Vertices are **deduplicated by exact position**: every triangle looks
    up each of its three vertices in a position -> index map and reuses
    the existing index when the same position has already been emitted.
    Triangles that share a physical edge therefore share the same pair of
    indices in the ``<triangle>`` entries. Without this, Bambu Studio /
    OrcaSlicer's manifold analysis (which walks edges by index, not by
    position) flags every edge as non-manifold even though the mesh is
    geometrically watertight.

    Deduplication uses the raw float32 byte representation as the key, so
    bit-exact positions from the geometry layer (which builds boxes on a
    shared grid) collapse to the same vertex entry without any tolerance
    heuristics.
    """
    tris = mesh.vectors  # shape (N, 3, 3) float32
    n_tris = int(tris.shape[0]) if tris.size else 0

    if n_tris == 0:
        return (
            f'<object id="{object_id}" type="model"><mesh><vertices/><triangles/></mesh></object>'
        )

    vertex_index: dict[bytes, int] = {}
    vertices_in_order: list[tuple[float, float, float]] = []
    triangle_indices: list[tuple[int, int, int]] = []
    for i in range(n_tris):
        idx: list[int] = []
        for j in range(3):
            v = tris[i, j]  # float32 (3,)
            key = v.tobytes()
            found = vertex_index.get(key)
            if found is None:
                found = len(vertices_in_order)
                vertex_index[key] = found
                vertices_in_order.append((float(v[0]), float(v[1]), float(v[2])))
            idx.append(found)
        triangle_indices.append((idx[0], idx[1], idx[2]))

    verts_parts = [
        f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>' for (x, y, z) in vertices_in_order
    ]
    tris_parts = [f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for (a, b, c) in triangle_indices]
    return (
        f'<object id="{object_id}" type="model">'
        f"<mesh><vertices>{''.join(verts_parts)}</vertices>"
        f"<triangles>{''.join(tris_parts)}</triangles></mesh>"
        f"</object>"
    )


def _model_xml(base: Mesh, features: Mesh) -> str:
    """Serialize the base + (optional) features as a multi-object 3MF model.

    The base always becomes ``objectid=1``. If the features mesh has at
    least one triangle it's added as ``objectid=2`` and the ``<build>``
    references both. Otherwise only the base is referenced.
    """
    objects = [_object_xml(1, base)]
    items = ['<item objectid="1"/>']
    has_features = bool(features.vectors.size) and int(features.vectors.shape[0]) > 0
    if has_features:
        objects.append(_object_xml(2, features))
        items.append('<item objectid="2"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{_CORE_NS}">'
        f"<resources>{''.join(objects)}</resources>"
        f"<build>{''.join(items)}</build>"
        "</model>"
    )


def _write_zip_part(zf: zipfile.ZipFile, name: str, data: str) -> None:
    """Write a single package part with the fixed deterministic timestamp."""
    info = zipfile.ZipInfo(filename=name, date_time=_FIXED_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data.encode("utf-8"))


def write_3mf(base: Mesh, features: Mesh, path: Path) -> Path:
    """Write ``base`` and ``features`` as a two-object 3MF at ``path``.

    Args:
        base: Base plate mesh (``objectid=1`` in the resulting 3MF).
        features: QR + text feature mesh. Emitted as ``objectid=2`` when
            non-empty; omitted otherwise (the resulting 3MF has a single
            object).
        path: Destination path. The ``.3mf`` suffix is appended when
            missing. Parent directories are created if needed.

    Returns:
        The absolute path that was written (with the ``.3mf`` suffix).
    """
    path = Path(path)
    if path.suffix.lower() != ".3mf":
        path = path.with_suffix(".3mf")
    path.parent.mkdir(parents=True, exist_ok=True)

    model_xml = _model_xml(base, features)

    # Build the ZIP in memory first so a half-written file never lands on
    # disk (any serialization error raises before we touch `path`).
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        _write_zip_part(zf, "[Content_Types].xml", _CONTENT_TYPES_XML)
        _write_zip_part(zf, "_rels/.rels", _RELS_XML)
        _write_zip_part(zf, "3D/3dmodel.model", model_xml)
    path.write_bytes(buf.getvalue())
    return path
