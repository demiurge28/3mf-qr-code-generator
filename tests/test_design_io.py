"""Tests for :mod:`qr23mf.design_io`.

Covers the JSON codec (round-trip, schema-version handling, unknown-key
tolerance, missing-key defaults, type errors), file I/O helpers
(:func:`save_design` / :func:`load_design` — suffix, encoding, parent
creation, malformed JSON), and the cross-platform :class:`Recents` store
(MRU ordering, cap, path normalisation, corrupt-store tolerance).
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from qr23mf.design_io import (
    Design,
    Recents,
    design_from_dict,
    design_to_dict,
    load_design,
    save_design,
)
from qr23mf.geometry import GeometryParams, QrPlacement, TextLabel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_design() -> Design:
    """A Design with every field set to a non-default value.

    Round-trip tests that use this fixture exercise every branch of
    ``design_to_dict`` / ``design_from_dict`` (non-default plate, non-null
    qr size, non-flush finish, non-threemf output, non-square module style,
    multiple text labels with distinct heights).
    """
    return Design(
        payload="https://example.com/a?q=1",
        ec="H",
        plate=GeometryParams(
            size_mm=80.0,
            base_height_mm=3.0,
            pixel_height_mm=1.5,
            quiet_zone_modules=6,
            depth_mm=50.0,
        ),
        qr=QrPlacement(qr_size_mm=40.0, x_offset_mm=5.0, y_offset_mm=-3.5),
        module_style="dot",
        finish="sunken",
        output="svg",
        text_labels=(
            TextLabel(content="qr23mf", x_mm=0.0, y_mm=-20.0, height_mm=6.0, extrusion_mm=1.0),
            TextLabel(content="v1.10.1", x_mm=0.0, y_mm=-27.0, height_mm=4.0, extrusion_mm=0.8),
        ),
    )


# ---------------------------------------------------------------------------
# design_to_dict / design_from_dict
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_every_field(populated_design: Design) -> None:
    d = design_from_dict(design_to_dict(populated_design))
    assert d == populated_design


def test_default_design_serialises_with_version_and_defaults() -> None:
    d = Design()
    raw = design_to_dict(d)
    assert raw["version"] == 1
    assert raw["payload"] == "https://example.com"
    assert raw["ec"] == "M"
    assert raw["module_style"] == "square"
    assert raw["finish"] == "flush"
    assert raw["output"] == "threemf"
    assert raw["plate"]["size_mm"] == 60.0
    assert raw["plate"]["depth_mm"] is None
    assert raw["qr"]["qr_size_mm"] is None
    assert raw["text_labels"] == []


def test_from_dict_fills_defaults_when_keys_missing() -> None:
    d = design_from_dict({"version": 1})
    assert d == Design()


def test_from_dict_warns_on_unknown_top_level_keys() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        d = design_from_dict({"version": 1, "surprise": 42, "another": "x"})
    messages = [str(w.message) for w in caught]
    assert any("surprise" in m and "another" in m for m in messages), messages
    # Even with the warning the parse succeeds and returns defaults.
    assert d == Design()


def test_from_dict_warns_when_schema_version_is_newer() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        design_from_dict({"version": 999, "payload": "hi"})
    assert any("version 999" in str(w.message) for w in caught)


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ({"version": "1"}, "'version' must be an int"),
        ({"ec": "X"}, "'ec' must be one of L/M/Q/H"),
        ({"module_style": "triangle"}, "'module_style' must be"),
        ({"finish": "sanded"}, "'finish' must be one of"),
        ({"output": "stl"}, "'output' must be"),
        ({"plate": []}, "'plate' must be a JSON object"),
        ({"plate": {"size_mm": "sixty"}}, "'plate.size_mm' must be a number"),
        ({"plate": {"quiet_zone_modules": 1.5}}, "'plate.quiet_zone_modules' must be an int"),
        ({"qr": {"qr_size_mm": True}}, "'qr.qr_size_mm' must be a number or null"),
        ({"text_labels": "no"}, "'text_labels' must be a JSON array"),
        ({"text_labels": [{}]}, "missing required 'content'"),
        ({"text_labels": [{"content": 3}]}, "text_labels[0].content"),
        ({"text_labels": [{"content": "hi", "height_mm": 0}]}, "height_mm"),
        ({"payload": 42}, "'payload' must be a string"),
    ],
)
def test_from_dict_rejects_malformed_input(payload: dict[str, object], needle: str) -> None:
    with pytest.raises(ValueError) as exc:
        design_from_dict(payload)
    assert needle in str(exc.value), (needle, str(exc.value))


def test_from_dict_rejects_non_dict_root() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        design_from_dict([])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# save_design / load_design
# ---------------------------------------------------------------------------


def test_save_design_appends_json_suffix(tmp_path: Path, populated_design: Design) -> None:
    p = save_design(populated_design, tmp_path / "mydesign")
    assert p.suffix == ".json"
    assert p.exists()


def test_save_creates_parent_directories(tmp_path: Path, populated_design: Design) -> None:
    target = tmp_path / "a" / "b" / "c" / "design.json"
    p = save_design(populated_design, target)
    assert p.exists()
    assert p.parent == target.parent.resolve()


def test_save_then_load_roundtrip(tmp_path: Path, populated_design: Design) -> None:
    p = save_design(populated_design, tmp_path / "coaster")
    loaded = load_design(p)
    assert loaded == populated_design


def test_load_rejects_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "broken.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_design(bad)
    assert "line" in str(exc.value)  # error includes position info


def test_load_rejects_non_object_root(tmp_path: Path) -> None:
    bad = tmp_path / "array.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="top-level JSON must be an object"):
        load_design(bad)


def test_load_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_design(tmp_path / "does-not-exist.json")


def test_saved_file_is_utf8_and_ends_with_newline(tmp_path: Path, populated_design: Design) -> None:
    p = save_design(populated_design, tmp_path / "utf.json")
    raw = p.read_bytes()
    assert raw.endswith(b"\n")
    # Ensure we can decode as UTF-8 without surrogate escapes.
    text = raw.decode("utf-8")
    assert json.loads(text)["payload"] == "https://example.com/a?q=1"


# ---------------------------------------------------------------------------
# Recents
# ---------------------------------------------------------------------------


def _write_touch(path: Path) -> Path:
    """Create an empty file on disk and return its resolved path.

    Recents prunes non-existent paths on load, so tests need real files on
    disk to populate the store.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path.resolve()


def test_recents_add_is_mru_ordered(tmp_path: Path) -> None:
    r = Recents()
    a = _write_touch(tmp_path / "a.json")
    b = _write_touch(tmp_path / "b.json")
    c = _write_touch(tmp_path / "c.json")
    r.add(a)
    r.add(b)
    r.add(c)
    assert list(r.paths) == [c, b, a]


def test_recents_add_deduplicates_on_resolve(tmp_path: Path) -> None:
    target = _write_touch(tmp_path / "same.json")
    r = Recents()
    r.add(target)
    # Relative alias of the same file should collapse, not add a duplicate.
    r.add(target.parent / "same.json")
    # Trailing "./" also collapses.
    r.add(str(target).replace(target.name, "./" + target.name))
    assert len(r) == 1
    assert list(r.paths) == [target]


def test_recents_caps_at_8(tmp_path: Path) -> None:
    r = Recents()
    created = [_write_touch(tmp_path / f"f{i}.json") for i in range(12)]
    for p in created:
        r.add(p)
    assert len(r) == 8
    # Most-recently-added 8 should survive; oldest 4 evicted.
    assert list(r.paths) == list(reversed(created))[:8]


def test_recents_readd_promotes_to_front(tmp_path: Path) -> None:
    a = _write_touch(tmp_path / "a.json")
    b = _write_touch(tmp_path / "b.json")
    c = _write_touch(tmp_path / "c.json")
    r = Recents()
    r.add(a)
    r.add(b)
    r.add(c)
    r.add(a)  # promote oldest to front
    assert list(r.paths) == [a, c, b]


def test_recents_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = tmp_path / "recents.json"
    r = Recents()
    r.add(_write_touch(tmp_path / "d1.json"))
    r.add(_write_touch(tmp_path / "d2.json"))
    r.save(store)
    loaded = Recents.load(store)
    assert list(loaded.paths) == list(r.paths)


def test_recents_load_prunes_missing_files(tmp_path: Path) -> None:
    store = tmp_path / "recents.json"
    existing = _write_touch(tmp_path / "present.json")
    missing = tmp_path / "gone.json"
    store.write_text(
        json.dumps({"paths": [str(missing), str(existing)]}),
        encoding="utf-8",
    )
    loaded = Recents.load(store)
    assert list(loaded.paths) == [existing]


def test_recents_load_tolerates_missing_store(tmp_path: Path) -> None:
    r = Recents.load(tmp_path / "nope.json")
    assert list(r.paths) == []


def test_recents_load_tolerates_corrupt_store(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not { valid", encoding="utf-8")
    r = Recents.load(bad)
    assert list(r.paths) == []


def test_recents_load_tolerates_wrong_shape(tmp_path: Path) -> None:
    store = tmp_path / "shape.json"
    # Root is a list, not an object \u2014 must not crash.
    store.write_text("[]", encoding="utf-8")
    assert list(Recents.load(store).paths) == []


def test_recents_clear_empties_in_memory(tmp_path: Path) -> None:
    r = Recents()
    r.add(_write_touch(tmp_path / "x.json"))
    assert len(r) == 1
    r.clear()
    assert len(r) == 0
    assert list(r) == []


def test_recents_extend_preserves_mru(tmp_path: Path) -> None:
    a = _write_touch(tmp_path / "a.json")
    b = _write_touch(tmp_path / "b.json")
    c = _write_touch(tmp_path / "c.json")
    r = Recents()
    r.extend([a, b, c])
    # ``extend`` calls ``add`` per-element, so last-added wins the front slot.
    assert list(r.paths) == [c, b, a]


def test_recents_as_list_is_snapshot(tmp_path: Path) -> None:
    r = Recents()
    r.add(_write_touch(tmp_path / "x.json"))
    snap = r.as_list()
    r.clear()
    # ``as_list`` must not reflect subsequent mutations.
    assert len(snap) == 1
    assert len(r) == 0


def test_recents_load_skips_non_string_items(tmp_path: Path) -> None:
    store = tmp_path / "mixed.json"
    real = _write_touch(tmp_path / "real.json")
    store.write_text(
        json.dumps({"paths": [42, None, str(real), {"no": "bueno"}]}),
        encoding="utf-8",
    )
    loaded = Recents.load(store)
    assert list(loaded.paths) == [real]


def test_recents_default_store_path_posix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When ``store_path`` is omitted, Recents uses the per-OS default.

    On POSIX that's ``$XDG_CONFIG_HOME/qr23mf/recents.json`` (falling back
    to ``~/.config/qr23mf/recents.json``). We can't safely touch the real
    user config dir from tests, so we redirect XDG_CONFIG_HOME to a temp
    directory and exercise the full round-trip there.
    """
    import sys as real_sys

    monkeypatch.setattr(real_sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)

    r = Recents()
    real = _write_touch(tmp_path / "design.json")
    r.add(real)
    out_path = r.save()  # default store path under XDG_CONFIG_HOME
    expected = tmp_path / "qr23mf" / "recents.json"
    assert out_path == expected.resolve()
    assert expected.exists()

    reloaded = Recents.load()
    assert list(reloaded.paths) == [real]


def test_recents_default_store_path_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On Windows-style platforms the default path lives under ``%APPDATA%``."""
    import sys as real_sys

    monkeypatch.setattr(real_sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    r = Recents()
    real = _write_touch(tmp_path / "design.json")
    r.add(real)
    out_path = r.save()
    expected = tmp_path / "qr23mf" / "recents.json"
    assert out_path == expected.resolve()
    assert expected.exists()


def test_recents_default_store_path_fallback_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Falls back to ``~/.config`` / ``~/AppData/Roaming`` when env is unset."""
    import sys as real_sys

    # POSIX fallback: neither XDG_CONFIG_HOME nor APPDATA set; Home.home()
    # is redirected to a temp dir so we don't poke the real user config.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(real_sys, "platform", "linux")

    from qr23mf.design_io import _recents_store_path

    p = _recents_store_path()
    assert p == fake_home / ".config" / "qr23mf" / "recents.json"

    # Windows fallback path.
    monkeypatch.setattr(real_sys, "platform", "win32")
    p_win = _recents_store_path()
    assert p_win == fake_home / "AppData" / "Roaming" / "qr23mf" / "recents.json"
