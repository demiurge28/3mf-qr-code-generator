"""Tests for the bootstrap CLI skeleton.

These tests only cover behavior that exists at bootstrap time: ``--help`` and
``--version``. The real generator command is added by the ``typer-cli`` scope.
"""

from __future__ import annotations

from typer.testing import CliRunner

from qr2stl import __version__
from qr2stl.cli import app

runner = CliRunner()


def test_help_exits_zero_and_prints_program_name() -> None:
    """``qr2stl --help`` must succeed and mention the binary name."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "qr2stl" in result.stdout.lower()


def test_version_flag_prints_version_and_exits_zero() -> None:
    """``qr2stl --version`` must print the package version and exit 0."""
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
    assert "qr2stl" in result.stdout.lower() or "qr2stl" in result.output.lower()
