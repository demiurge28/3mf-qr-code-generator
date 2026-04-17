"""Typer CLI skeleton for qr23mf.

Bootstrap scope (see ``vbrief/active/2026-04-17-bootstrap-python-skeleton.vbrief.json``)
only wires up ``--help`` and ``--version``. The real ``generate`` command lands in
the ``typer-cli`` scope once the geometry/writer scopes are implemented.
"""

from __future__ import annotations

from typing import Annotated

import typer

from qr23mf import __version__

app = typer.Typer(
    name="qr23mf",
    help=(
        "Turn a string or URL into a 3D-printable QR code mesh (single-color STL "
        "or two-color 3MF). This command surface is currently a skeleton; run "
        "`qr23mf --help` to see available options."
    ),
    no_args_is_help=True,
    add_completion=True,
)


def _version_callback(value: bool) -> None:
    """Print the installed qr23mf version and exit."""
    if value:
        typer.echo(f"qr23mf {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the qr23mf version and exit.",
            is_eager=True,
            callback=_version_callback,
        ),
    ] = False,
) -> None:
    """qr23mf root command.

    Subcommands land here as later scopes ship. At the moment the command is a
    placeholder and exits via ``no_args_is_help`` when invoked without options.
    """
    # typer's `is_eager` version callback exits before we get here; the unused
    # `version` parameter satisfies strict typing without enabling behavior.
    del version


if __name__ == "__main__":  # pragma: no cover - manual invocation only
    app()
