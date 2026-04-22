"""Design persistence for the qr23mf GUI.

The Tkinter GUI (:mod:`qr23mf.gui`) captures a fairly dense configuration
(payload, error-correction level, plate dimensions, QR placement, module
style, finish, output format, and text labels) that gets thrown away on
window close. This module defines a small, version-stamped JSON codec so a
user can name and round-trip their designs, plus a cross-platform
"recently opened" store that the File menu can populate.

The design of this module is intentionally minimal:

- No Tkinter imports \u2014 the codec is pure and fully testable without a live
  Tk interpreter.
- No reliance on private GUI state \u2014 :class:`Design` mirrors the tuple
  returned by ``_SettingsApp._gather_design`` plus the output format, so
  the GUI can construct a :class:`Design` by calling public getters and
  drive itself back into a :class:`Design` without any round-trip surprises.
- The JSON schema is versioned (``version: 1``) and forward-tolerant:
  unknown top-level keys emit a :mod:`warnings` warning rather than
  raising, and missing keys fall back to module defaults from
  :mod:`qr23mf.geometry`. Adding an optional field later therefore doesn't
  require a schema bump.

Tracks: the ``gui-saved-designs`` vbrief
(``vbrief/active/2026-04-22-gui-saved-designs.vbrief.json``).
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal, cast

from qr23mf.geometry import GeometryParams, QrPlacement, TextLabel

__all__ = [
    "Design",
    "Recents",
    "design_from_dict",
    "design_to_dict",
    "load_design",
    "save_design",
]

#: Current on-disk schema version. Bump only on *breaking* changes \u2014 additive
#: fields should stay at version 1 and rely on missing-key defaulting.
_SCHEMA_VERSION: Final[int] = 1

#: Top-level keys accepted by :func:`design_from_dict` without a warning.
#: Unknown keys trigger a :class:`UserWarning` but do not raise.
_KNOWN_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "version",
        "payload",
        "ec",
        "plate",
        "qr",
        "module_style",
        "finish",
        "output",
        "text_labels",
    }
)

#: Cap for the MRU recent-files list. Matches what the File menu can
#: reasonably display without scrolling on macOS's 25-row-tall default menu.
_RECENTS_MAX: Final[int] = 8

#: Filename used under the per-OS config directory for the recents store.
_RECENTS_FILENAME: Final[str] = "recents.json"

EcLevel = Literal["L", "M", "Q", "H"]
ModuleStyle = Literal["square", "dot"]
QrFinish = Literal["extruded", "flush", "sunken"]
OutputFormat = Literal["threemf", "svg"]


@dataclass(slots=True)
class Design:
    """Snapshot of every setting the GUI exposes.

    Intentionally a plain dataclass rather than the frozen geometry primitives
    because the GUI mutates fields in place as the user edits the form. A
    :class:`Design` is converted to / from the frozen primitives at the
    boundaries (``_gather_design``, ``build_meshes``) when geometry is
    actually built.
    """

    payload: str = "https://example.com"
    ec: EcLevel = "M"
    plate: GeometryParams = field(default_factory=GeometryParams)
    qr: QrPlacement = field(default_factory=QrPlacement)
    module_style: ModuleStyle = "square"
    finish: QrFinish = "flush"
    output: OutputFormat = "threemf"
    text_labels: tuple[TextLabel, ...] = ()


# ---------------------------------------------------------------------------
# JSON codec
# ---------------------------------------------------------------------------


def design_to_dict(design: Design) -> dict[str, Any]:
    """Serialise a :class:`Design` to a JSON-compatible dict.

    The returned dict matches the schema documented in this module's
    docstring and is stable under ``json.dumps`` \u2014 dict iteration order
    here is deterministic so textual diffs between saves stay minimal.
    """
    return {
        "version": _SCHEMA_VERSION,
        "payload": design.payload,
        "ec": design.ec,
        "plate": {
            "size_mm": float(design.plate.size_mm),
            "depth_mm": (None if design.plate.depth_mm is None else float(design.plate.depth_mm)),
            "base_height_mm": float(design.plate.base_height_mm),
            "pixel_height_mm": float(design.plate.pixel_height_mm),
            "quiet_zone_modules": int(design.plate.quiet_zone_modules),
        },
        "qr": {
            "qr_size_mm": (None if design.qr.qr_size_mm is None else float(design.qr.qr_size_mm)),
            "x_offset_mm": float(design.qr.x_offset_mm),
            "y_offset_mm": float(design.qr.y_offset_mm),
        },
        "module_style": design.module_style,
        "finish": design.finish,
        "output": design.output,
        "text_labels": [
            {
                "content": label.content,
                "x_mm": float(label.x_mm),
                "y_mm": float(label.y_mm),
                "height_mm": float(label.height_mm),
                "extrusion_mm": float(label.extrusion_mm),
            }
            for label in design.text_labels
        ],
    }


def _require_dict(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name!r} must be a JSON object, got {type(value).__name__}")
    return cast(dict[str, Any], value)


def _require_str(value: Any, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name!r} must be a string, got {type(value).__name__}")
    return value


def _optional_float(value: Any, default: float, *, name: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name!r} must be a number, got {type(value).__name__}")
    return float(value)


def _optional_float_or_none(value: Any, default: float | None, *, name: str) -> float | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name!r} must be a number or null, got {type(value).__name__}")
    return float(value)


def _optional_int(value: Any, default: int, *, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name!r} must be an int, got {type(value).__name__}")
    return int(value)


def _narrow_ec(value: Any, default: EcLevel) -> EcLevel:
    if value is None:
        return default
    if not isinstance(value, str) or value not in ("L", "M", "Q", "H"):
        raise ValueError(f"'ec' must be one of L/M/Q/H, got {value!r}")
    return cast(EcLevel, value)


def _narrow_module_style(value: Any, default: ModuleStyle) -> ModuleStyle:
    if value is None:
        return default
    if value not in ("square", "dot"):
        raise ValueError(f"'module_style' must be 'square' or 'dot', got {value!r}")
    return cast(ModuleStyle, value)


def _narrow_finish(value: Any, default: QrFinish) -> QrFinish:
    if value is None:
        return default
    if value not in ("extruded", "flush", "sunken"):
        raise ValueError(f"'finish' must be one of extruded/flush/sunken, got {value!r}")
    return cast(QrFinish, value)


def _narrow_output(value: Any, default: OutputFormat) -> OutputFormat:
    if value is None:
        return default
    if value not in ("threemf", "svg"):
        raise ValueError(f"'output' must be 'threemf' or 'svg', got {value!r}")
    return cast(OutputFormat, value)


def _parse_text_labels(raw: Any) -> tuple[TextLabel, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"'text_labels' must be a JSON array, got {type(raw).__name__}")
    out: list[TextLabel] = []
    for i, item in enumerate(raw):
        ctx = f"text_labels[{i}]"
        d = _require_dict(item, name=ctx)
        if "content" not in d:
            raise ValueError(f"{ctx}: missing required 'content'")
        content = _require_str(d["content"], name=f"{ctx}.content")
        try:
            label = TextLabel(
                content=content,
                x_mm=_optional_float(d.get("x_mm"), 0.0, name=f"{ctx}.x_mm"),
                y_mm=_optional_float(d.get("y_mm"), 0.0, name=f"{ctx}.y_mm"),
                height_mm=_optional_float(d.get("height_mm"), 5.0, name=f"{ctx}.height_mm"),
                extrusion_mm=_optional_float(
                    d.get("extrusion_mm"), 1.0, name=f"{ctx}.extrusion_mm"
                ),
            )
        except ValueError as exc:
            raise ValueError(f"{ctx}: {exc}") from exc
        out.append(label)
    return tuple(out)


def design_from_dict(payload: dict[str, Any]) -> Design:
    """Parse a dict (typically from JSON) into a :class:`Design`.

    Missing top-level keys fall back to module defaults (``GeometryParams()``,
    ``QrPlacement()``, no text labels, etc.). Unknown top-level keys trigger
    a :class:`UserWarning` but don't raise \u2014 that lets future optional fields
    land without blowing up an older qr23mf build that reads the new file.
    """
    _require_dict(payload, name="root")

    version = payload.get("version", _SCHEMA_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f"'version' must be an int, got {type(version).__name__}")
    if version > _SCHEMA_VERSION:
        warnings.warn(
            f"Design file declares schema version {version} but this qr23mf "
            f"build only knows version {_SCHEMA_VERSION}; unknown fields will "
            f"be ignored and defaults used.",
            stacklevel=2,
        )

    unknown = set(payload.keys()) - _KNOWN_TOP_LEVEL_KEYS
    if unknown:
        warnings.warn(
            f"Ignoring unknown design keys: {sorted(unknown)}",
            stacklevel=2,
        )

    plate_raw: dict[str, Any] = {}
    if "plate" in payload:
        plate_raw = _require_dict(payload["plate"], name="plate")
    plate_defaults = GeometryParams()
    plate = GeometryParams(
        size_mm=_optional_float(
            plate_raw.get("size_mm"), plate_defaults.size_mm, name="plate.size_mm"
        ),
        base_height_mm=_optional_float(
            plate_raw.get("base_height_mm"),
            plate_defaults.base_height_mm,
            name="plate.base_height_mm",
        ),
        pixel_height_mm=_optional_float(
            plate_raw.get("pixel_height_mm"),
            plate_defaults.pixel_height_mm,
            name="plate.pixel_height_mm",
        ),
        quiet_zone_modules=_optional_int(
            plate_raw.get("quiet_zone_modules"),
            plate_defaults.quiet_zone_modules,
            name="plate.quiet_zone_modules",
        ),
        depth_mm=_optional_float_or_none(
            plate_raw.get("depth_mm"),
            plate_defaults.depth_mm,
            name="plate.depth_mm",
        ),
    )

    qr_raw: dict[str, Any] = {}
    if "qr" in payload:
        qr_raw = _require_dict(payload["qr"], name="qr")
    qr_defaults = QrPlacement()
    qr = QrPlacement(
        qr_size_mm=_optional_float_or_none(
            qr_raw.get("qr_size_mm"), qr_defaults.qr_size_mm, name="qr.qr_size_mm"
        ),
        x_offset_mm=_optional_float(
            qr_raw.get("x_offset_mm"),
            qr_defaults.x_offset_mm,
            name="qr.x_offset_mm",
        ),
        y_offset_mm=_optional_float(
            qr_raw.get("y_offset_mm"),
            qr_defaults.y_offset_mm,
            name="qr.y_offset_mm",
        ),
    )

    return Design(
        payload=_require_str(payload.get("payload", "https://example.com"), name="payload"),
        ec=_narrow_ec(payload.get("ec"), "M"),
        plate=plate,
        qr=qr,
        module_style=_narrow_module_style(payload.get("module_style"), "square"),
        finish=_narrow_finish(payload.get("finish"), "flush"),
        output=_narrow_output(payload.get("output"), "threemf"),
        text_labels=_parse_text_labels(payload.get("text_labels")),
    )


def save_design(design: Design, path: str | os.PathLike[str]) -> Path:
    """Write ``design`` as pretty-printed JSON to ``path``.

    The ``.json`` suffix is appended when missing; parent directories are
    created as needed. Returns the absolute :class:`Path` that was written.
    """
    p = Path(os.fspath(path))
    if p.suffix.lower() != ".json":
        p = p.with_suffix(".json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(design_to_dict(design), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return p.resolve()


def load_design(path: str | os.PathLike[str]) -> Design:
    """Read a design JSON file.

    Raises :class:`FileNotFoundError` when the path is missing,
    :class:`OSError` on unreadable files, and :class:`ValueError` when the
    JSON is syntactically valid but semantically rejected (wrong types,
    missing required fields, invalid enum values). JSON decode errors from
    :mod:`json` are re-raised as :class:`ValueError` so a caller only needs
    to handle ``(OSError, ValueError)``.
    """
    p = Path(os.fspath(path))
    text = p.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{p}: {exc.msg} at line {exc.lineno} column {exc.colno}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{p}: top-level JSON must be an object, got {type(payload).__name__}")
    return design_from_dict(payload)


# ---------------------------------------------------------------------------
# Recent-files store
# ---------------------------------------------------------------------------


def _recents_store_path() -> Path:
    """Return the per-OS path to the persistent recent-files JSON.

    Honours ``$XDG_CONFIG_HOME`` on POSIX and ``%APPDATA%`` on Windows, with
    sensible fallbacks (``~/.config`` / ``~/AppData/Roaming``) when those
    env vars are unset. Never creates directories \u2014 that's the caller's job
    on save.
    """
    if sys.platform.startswith("win"):
        base_str = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        base = Path(base_str)
    else:
        base_str = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        base = Path(base_str)
    return base / "qr23mf" / _RECENTS_FILENAME


@dataclass(slots=True)
class Recents:
    """MRU list of recently-saved / -loaded design paths.

    - Capped at :data:`_RECENTS_MAX` entries; oldest entries are evicted on
      overflow.
    - Deduplicates on ``Path.resolve()`` so ``./foo.json`` and
      ``/abs/foo.json`` collapse to one entry.
    - :meth:`load` tolerates a missing or corrupt store by returning an
      empty :class:`Recents`; :meth:`save` creates parent directories.
    """

    paths: list[Path] = field(default_factory=list)

    @classmethod
    def load(cls, store_path: Path | None = None) -> Recents:
        target = store_path or _recents_store_path()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        items_raw = raw.get("paths", [])
        if not isinstance(items_raw, list):
            return cls()
        paths: list[Path] = []
        seen: set[Path] = set()
        for item in items_raw:
            if not isinstance(item, str):
                continue
            try:
                resolved = Path(item).expanduser().resolve()
            except OSError:
                continue
            if not resolved.exists() or resolved in seen:
                continue
            paths.append(resolved)
            seen.add(resolved)
            if len(paths) >= _RECENTS_MAX:
                break
        return cls(paths=paths)

    def save(self, store_path: Path | None = None) -> Path:
        target = store_path or _recents_store_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"paths": [str(p) for p in self.paths[:_RECENTS_MAX]]}
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return target.resolve()

    def add(self, path: str | os.PathLike[str]) -> None:
        """Record ``path`` as the most-recent entry (dedup + cap)."""
        resolved = Path(os.fspath(path)).expanduser().resolve()
        # Remove any prior occurrence so the new one lands at the front.
        self.paths = [p for p in self.paths if p != resolved]
        self.paths.insert(0, resolved)
        if len(self.paths) > _RECENTS_MAX:
            self.paths = self.paths[:_RECENTS_MAX]

    def extend(self, paths: Iterable[str | os.PathLike[str]]) -> None:
        """Add every path in ``paths``, preserving MRU semantics."""
        for p in paths:
            self.add(p)

    def clear(self) -> None:
        """Empty the list. Does not delete the persisted store file."""
        self.paths.clear()

    def __iter__(self) -> Any:
        return iter(self.paths)

    def __len__(self) -> int:
        return len(self.paths)

    def as_list(self) -> Sequence[Path]:
        """Return a read-only snapshot of the current MRU order."""
        return tuple(self.paths)
