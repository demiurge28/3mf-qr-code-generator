"""Typer CLI for qr23mf.

Wires the ``qr``, ``geometry``, and ``writers.threemf`` layers into a
user-facing command surface. The ``generate`` subcommand runs the
in-memory pipeline (text -> QrMatrix -> base + features meshes) and
optionally writes a two-object 3MF package to disk so slicers can
assign a different filament to each body for two-color prints.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from qr23mf import __version__
from qr23mf.geometry import GeometryParams, build_meshes
from qr23mf.qr import EcLevel, build_matrix
from qr23mf.writers.svg import write_svg
from qr23mf.writers.threemf import write_3mf

app = typer.Typer(
    name="qr23mf",
    help=(
        "Turn a string or URL into a 3D-printable QR code mesh. Run "
        "`qr23mf generate --help` for the end-to-end command, or `qr23mf "
        "--help` to see top-level options."
    ),
    no_args_is_help=True,
    add_completion=True,
)

_EC_CHOICES = ("L", "M", "Q", "H")


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

    Subcommands expose the individual pipeline stages. Without arguments the
    full ``--help`` output is printed (typer's ``no_args_is_help`` behavior).
    """
    # typer's `is_eager` version callback exits before we get here; the unused
    # `version` parameter satisfies strict typing without enabling behavior.
    del version


def _print_summary(
    text: str, ec: EcLevel, params: GeometryParams, base_triangles: int, pixel_triangles: int
) -> None:
    """Print a human-readable summary of the generated meshes."""
    n_pixels = pixel_triangles // 12
    typer.echo("Generated QR mesh:")
    typer.echo(f"  text              {text!r}")
    typer.echo(f"  error correction  {ec}")
    typer.echo(f"  size              {params.size_mm:g} mm x {params.size_mm:g} mm")
    typer.echo(f"  base height       {params.base_height_mm:g} mm")
    typer.echo(f"  pixel height      {params.pixel_height_mm:g} mm")
    typer.echo(f"  quiet zone        {params.quiet_zone_modules} modules")
    typer.echo(f"  base triangles    {base_triangles}")
    typer.echo(f"  pixel boxes       {n_pixels}")
    typer.echo(f"  pixel triangles   {pixel_triangles}")
    typer.echo(f"  total triangles   {base_triangles + pixel_triangles}")


@app.command()
def generate(
    text: Annotated[
        str,
        typer.Option(
            "--text",
            "-t",
            help="Payload to encode (string or URL). Must be non-empty.",
        ),
    ],
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            "-o",
            help=(
                "Optional output path. If given, a two-object 3MF package is "
                "written (base and features as separate selectable bodies). "
                "The .3mf suffix is appended if missing."
            ),
        ),
    ] = None,
    size_mm: Annotated[
        float,
        typer.Option("--size", help="Base plate side length in millimeters."),
    ] = 60.0,
    base_height_mm: Annotated[
        float,
        typer.Option("--base-height", help="Base plate thickness in millimeters."),
    ] = 2.0,
    pixel_height_mm: Annotated[
        float,
        typer.Option("--pixel-height", help="Extrusion height of dark modules above the base."),
    ] = 1.0,
    ec: Annotated[
        str,
        typer.Option(
            "--ec",
            help="QR error-correction level. One of L, M, Q, H.",
        ),
    ] = "M",
    quiet_zone_modules: Annotated[
        int,
        typer.Option(
            "--quiet-zone",
            help="Quiet-zone margin in module units (QR spec recommends 4).",
        ),
    ] = 4,
) -> None:
    """Build a QR mesh from ``--text`` and print its geometry summary.

    Passing ``--out path.3mf`` additionally writes a 3MF package with two
    selectable objects (base + features) so any modern slicer can load
    them as separate bodies and assign a different filament to each.
    """
    ec_upper = ec.upper()
    if ec_upper not in _EC_CHOICES:
        typer.secho(
            f"Error: --ec must be one of {', '.join(_EC_CHOICES)} (got {ec!r}).",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    try:
        matrix = build_matrix(text, ec=ec_upper)  # type: ignore[arg-type]
        params = GeometryParams(
            size_mm=size_mm,
            base_height_mm=base_height_mm,
            pixel_height_mm=pixel_height_mm,
            quiet_zone_modules=quiet_zone_modules,
        )
        base, pixels = build_meshes(matrix, params)
    except ValueError as exc:
        typer.secho(f"Error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc

    _print_summary(
        text=text,
        ec=ec_upper,  # type: ignore[arg-type]
        params=params,
        base_triangles=base.vectors.shape[0],
        pixel_triangles=pixels.vectors.shape[0],
    )

    if out is not None:
        try:
            out_path = write_3mf(base, pixels, out)
        except OSError as exc:
            typer.secho(f"Error writing {out}: {exc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=3) from exc
        typer.echo(f"Wrote {out_path}")


@app.command()
def svg(
    text: Annotated[
        str,
        typer.Option(
            "--text",
            "-t",
            help="Payload to encode (string or URL). Must be non-empty.",
        ),
    ],
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            "-o",
            help=(
                "Output path for the SVG. The .svg suffix is appended if "
                "missing. Parent directories are created automatically."
            ),
        ),
    ],
    size_mm: Annotated[
        float,
        typer.Option("--size", help="Plate side length in millimetres (plate is square)."),
    ] = 60.0,
    ec: Annotated[
        str,
        typer.Option("--ec", help="QR error-correction level. One of L, M, Q, H."),
    ] = "M",
    quiet_zone_modules: Annotated[
        int,
        typer.Option(
            "--quiet-zone",
            help="Quiet-zone margin in module units (QR spec recommends 4).",
        ),
    ] = 4,
    module_style: Annotated[
        str,
        typer.Option(
            "--module-style",
            help="How each dark module is drawn: 'square' (default) or 'dot'.",
        ),
    ] = "square",
    fill: Annotated[
        str,
        typer.Option(
            "--fill",
            help="SVG fill color for the QR modules (CSS color, default black).",
        ),
    ] = "#000000",
    stroke: Annotated[
        str | None,
        typer.Option(
            "--stroke",
            help="Optional SVG stroke color for the QR modules.",
        ),
    ] = None,
    background: Annotated[
        str | None,
        typer.Option(
            "--background",
            help=(
                "Optional plate-footprint fill color. When set, a "
                "<rect> covering the full plate is emitted underneath "
                "the QR."
            ),
        ),
    ] = None,
    background_stroke: Annotated[
        str | None,
        typer.Option(
            "--background-stroke",
            help="Optional plate-footprint stroke color (e.g. for a cut layer).",
        ),
    ] = None,
    layer_per_feature: Annotated[
        bool,
        typer.Option(
            "--layer-per-feature",
            help=(
                "Wrap the plate footprint, QR modules, and each text "
                "label in their own <g class=...> so laser software "
                "imports them as independent layers."
            ),
        ),
    ] = False,
) -> None:
    """Write a 2D SVG of the QR code for laser etching / engraving.

    The SVG is emitted in millimetre units (``width="<N>mm"`` + matching
    ``viewBox``) so it imports at real-world size in Inkscape, Illustrator,
    and LightBurn. The same geometry math powers both ``generate`` (3MF)
    and ``svg``, so dimensions match exactly between the two outputs.
    """
    ec_upper = ec.upper()
    if ec_upper not in _EC_CHOICES:
        typer.secho(
            f"Error: --ec must be one of {', '.join(_EC_CHOICES)} (got {ec!r}).",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    style_lower = module_style.lower()
    if style_lower not in {"square", "dot"}:
        typer.secho(
            f"Error: --module-style must be 'square' or 'dot' (got {module_style!r}).",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    try:
        matrix = build_matrix(text, ec=ec_upper)  # type: ignore[arg-type]
        params = GeometryParams(
            size_mm=size_mm,
            # base_height / pixel_height are irrelevant for SVG output but
            # GeometryParams still validates them as positive, so seed the
            # defaults to satisfy the dataclass.
            base_height_mm=2.0,
            pixel_height_mm=1.0,
            quiet_zone_modules=quiet_zone_modules,
        )
    except ValueError as exc:
        typer.secho(f"Error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc

    try:
        out_path = write_svg(
            matrix,
            params,
            out,
            module_style=style_lower,  # type: ignore[arg-type]
            fill=fill,
            stroke=stroke,
            plate_fill=background,
            plate_stroke=background_stroke,
            layer_per_feature=layer_per_feature,
        )
    except ValueError as exc:
        typer.secho(f"Error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        typer.secho(f"Error writing {out}: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=3) from exc
    typer.echo(f"Wrote {out_path}")


@app.command()
def gui() -> None:
    """Launch the Tkinter GUI (plate / QR / text labels + preview + create).

    The GUI lets you pick the base-plate dimensions, QR code size and
    position on the plate, add text labels, choose between square and dot
    modules, and preview the layout before writing a two-object 3MF.

    Requires Python's Tk bindings. On macOS with Homebrew Python 3.11
    you may need to install them separately:

        brew install python-tk@3.11
    """
    try:
        from qr23mf.gui import run as run_gui
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing in {"tkinter", "_tkinter"}:
            typer.secho(
                "Error: Tkinter is not available in this Python install. "
                "On macOS with Homebrew, run: brew install python-tk@3.11",
                err=True,
                fg=typer.colors.RED,
            )
        elif missing.split(".", 1)[0] in {"PIL", "Pillow"}:
            typer.secho(
                "Error: Pillow is required for the GUI (text rasterization). "
                "Install it with: uv sync --all-extras  (or: pip install pillow)",
                err=True,
                fg=typer.colors.RED,
            )
        else:
            typer.secho(
                f"Error: failed to import qr23mf.gui ({exc}).", err=True, fg=typer.colors.RED
            )
        raise typer.Exit(code=2) from exc

    run_gui()


if __name__ == "__main__":  # pragma: no cover - manual invocation only
    app()
