"""Tests for the qr23mf CLI.

Covers the top-level ``--help`` / ``--version`` surface and the ``generate``
subcommand that wires the pipeline (text -> QrMatrix -> meshes -> optional
STL file) end-to-end.
"""

from __future__ import annotations

from pathlib import Path

from stl.mesh import Mesh
from typer.testing import CliRunner

from qr23mf import __version__
from qr23mf.cli import app

runner = CliRunner()


def test_help_exits_zero_and_prints_program_name() -> None:
    """``qr23mf --help`` must succeed and mention the binary name."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "qr23mf" in result.stdout.lower()


def test_version_flag_prints_version_and_exits_zero() -> None:
    """``qr23mf --version`` must print the package version and exit 0."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_no_args_shows_help_and_is_not_error() -> None:
    """Running with no args must display help (typer's ``no_args_is_help=True``)."""
    result = runner.invoke(app, [])
    # Typer emits help on stdout and returns exit code 2 by default when no args
    # are given with no_args_is_help=True; we accept either 0 or 2 as long as
    # help text was produced.
    assert result.exit_code in {0, 2}
    assert "qr23mf" in result.stdout.lower() or "qr23mf" in result.output.lower()


# --- generate subcommand ------------------------------------------------------


def test_generate_help_lists_all_flags() -> None:
    result = runner.invoke(app, ["generate", "--help"])
    assert result.exit_code == 0
    for flag in (
        "--text",
        "--out",
        "--size",
        "--base-height",
        "--pixel-height",
        "--ec",
        "--quiet-zone",
    ):
        assert flag in result.stdout


def test_generate_prints_summary_for_valid_input() -> None:
    result = runner.invoke(app, ["generate", "--text", "https://example.com"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "Generated QR mesh" in out
    assert "error correction  M" in out
    assert "base triangles    12" in out
    assert "total triangles" in out


def test_generate_respects_ec_flag_case_insensitively() -> None:
    """``--ec h`` should normalize to H without erroring."""
    result = runner.invoke(app, ["generate", "--text", "qr23mf", "--ec", "h"])
    assert result.exit_code == 0, result.output
    assert "error correction  H" in result.stdout


def test_generate_rejects_invalid_ec_with_exit_code_2() -> None:
    result = runner.invoke(app, ["generate", "--text", "hi", "--ec", "X"])
    assert result.exit_code == 2
    assert "--ec" in result.output


def test_generate_rejects_empty_text_with_exit_code_2() -> None:
    result = runner.invoke(app, ["generate", "--text", ""])
    assert result.exit_code == 2
    assert "non-empty" in result.output


def test_generate_rejects_tiny_size_with_exit_code_2() -> None:
    result = runner.invoke(app, ["generate", "--text", "hi", "--size", "1"])
    assert result.exit_code == 2
    assert "too small" in result.output


def test_generate_writes_stl_when_out_is_given(tmp_path: Path) -> None:
    out = tmp_path / "coaster.stl"
    result = runner.invoke(app, ["generate", "--text", "https://example.com", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert out.stat().st_size > 0
    # The file should load as a valid binary STL.
    loaded = Mesh.from_file(str(out))
    assert loaded.vectors.shape[0] >= 12  # at least the 12 base triangles
    assert f"Wrote {out}" in result.stdout


def test_generate_appends_stl_suffix_when_missing(tmp_path: Path) -> None:
    out = tmp_path / "coaster"  # no suffix
    result = runner.invoke(app, ["generate", "--text", "hi", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "coaster.stl").exists()
