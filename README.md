# 3mf QR Code Generator

`qr23mf` — turn a string or URL into a 3D-printable QR code mesh: two-color **3MF** with per-body color metadata, or single-color **STL**. Built as a small, pure-Python CLI.

> **Status:** early. The `generate` subcommand produces usable single-color STL output today; two-color 3MF output lands with the `3mf-writer` scope. Scope-by-scope work-in-progress lives in `vbrief/active/` and `vbrief/proposed/`.

## Install (development)

Requires [uv](https://docs.astral.sh/uv/) and optionally [Task](https://taskfile.dev/).

```bash
uv sync --all-extras
uv run qr23mf --help
```

## Install (end users)

```bash
uv tool install .
# or
pipx install .
```

Either command exposes a `qr23mf` binary on `PATH`.

## Usage

The CLI currently has one real subcommand, `generate`, plus top-level
`--help` / `--version` flags.

### Quickstart

```bash
# Print the mesh summary for a payload with sensible defaults
qr23mf generate --text "https://example.com"

# Additionally save a single-color binary STL to disk
qr23mf generate --text "https://example.com" --out coaster.stl

# Higher error-correction level, smaller plate, thicker pixels
qr23mf generate \
  --text "https://example.com" \
  --ec Q \
  --size 50 \
  --base-height 1.5 \
  --pixel-height 1.2 \
  --out keychain.stl
```

The command prints a human-readable summary that is handy for sanity-checking
the generated geometry before you slice it:

```
Generated QR mesh:
  text              'https://example.com'
  error correction  M
  size              60 mm x 60 mm
  base height       2 mm
  pixel height      1 mm
  quiet zone        4 modules
  base triangles    12
  pixel boxes       322
  pixel triangles   3864
  total triangles   3876
Wrote coaster.stl
```

### Flag reference

| Flag | Default | Description |
|---|---|---|
| `--text`, `-t` | _(required)_ | Payload to encode. Must be a non-empty string (URLs, plain text, etc.). |
| `--out`, `-o` | _unset_ | Optional output path. When provided, a single-body binary STL is written; if the path has no suffix, `.stl` is appended. |
| `--size` | `60.0` | Base plate side length in millimeters (plate is square). |
| `--base-height` | `2.0` | Base plate thickness in millimeters. |
| `--pixel-height` | `1.0` | Extrusion height of the dark QR modules above the base's top face. |
| `--ec` | `M` | QR error-correction level. One of `L`, `M`, `Q`, `H` (case-insensitive). Higher levels tolerate more damage but require a larger QR version. |
| `--quiet-zone` | `4` | Quiet-zone margin expressed in module units. The QR specification recommends 4. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `2` | Usage / validation error (unknown EC level, empty text, physical dimensions too small, etc.). Message written to stderr. |
| `3` | I/O error while writing `--out`. Message written to stderr. |

### Coordinate system

Meshes are produced in millimeter units and positioned so the base plate's
bottom face lies on `z = 0` and the plate is centered on the XY origin.
Row 0 of the QR matrix is placed along `+Y` (i.e. the "top" of the code when
viewed from `+Z`). Dark modules sit on top of the base as separate axis-
aligned boxes with outward-facing normals, so slicers see watertight solids.

### What's not here yet

* `--format 3mf` (two-color 3MF with embedded color metadata) — blocked on
  the `3mf-writer` scope.
* `--base-color` / `--pixel-color` — same.
* Batch mode, preset profiles, embossed vs debossed, logo overlay — later
  scopes tracked in `vbrief/proposed/`.

## GUI (Tkinter)

```bash
qr23mf gui
```

Opens a Tkinter configurator with a Settings window (the main designer)
and a Preview window (opened from **Preview**).

### Settings window

* **Payload** text and EC level (`L` / `M` / `Q` / `H`).
* **Base plate** width / depth / thickness (rectangular plates supported).
* **QR code** size and X / Y offset on the plate (`0 mm size` = auto-fit);
  module extrusion; quiet-zone margin.
* **Module style**: **Squares** (axis-aligned boxes) or **Dots**
  (cylindrical prisms).
* **Finish**: **Extruded** (raised above the plate, default), **Flush**
  (pixels embedded in the plate top slab), or **Sunken** (pixels occupy
  the top slab *and* the base has matching pockets carved into its top
  face, so the QR is visibly recessed even in single-color prints).
* **Text labels** — add, update, remove, or **Remove all**; each label has
  its own text, X / Y position, height, and extrusion.
* **Check for updates** button — queries the public GitHub Releases API
  (5 s timeout) and shows "No New Updates" when current, or offers to
  open the Releases page in your browser when a newer tag is available.

### Interactive layout canvas

A live top-down canvas sits next to the text-label form. It always shows
the plate outline, the QR footprint (dashed), and every text label. All
spinbox edits redraw it live.

Mouse / trackpad interactions:

* **Left-click on empty plate** — adds a new text label at that point
  using the current Text / Height / Extrusion form values.
* **Left-click + drag a label** — moves it, clamped to the plate bounds;
  the X / Y spinboxes and listbox entry update in real time.
* **Right-click a label** (or **Ctrl+Click** on macOS) — removes it with
  a confirmation dialog.
* **Left-click inside the QR footprint** — selects the QR (highlighted
  with an orange dashed outline). With the QR selected, the arrow keys
  **← → ↑ ↓** nudge it in 0.5 mm steps, clamped to keep it inside the
  plate.

Three toggles below the canvas:

* **Grid (5 mm)** — overlays a 5 mm alignment grid on the plate. Every
  5th line is rendered heavier (25 mm major spacing for free).
* **Snap** — when enabled, label drags and click-to-add both snap
  independently on X and Y to the nearest alignment anchor within 1 mm.
  Anchors include the plate center, plate edges, QR center and edges,
  every other label's center, and — when the grid is also on — every
  grid line. Moves that land more than 1 mm from any anchor pass through
  unchanged.
* **Show spacing** — when enabled, the currently selected label is
  annotated with dashed blue guides and mm distances from each side of
  its bounding box to the nearest plate edge, plus to the nearest QR
  footprint edge in X / Y when the label doesn't overlap the QR along
  that axis.

### Preview window

Press **Preview** to open a 2D top-down preview with a one-line summary
(plate dims, style, finish, label count, triangle counts):

* **Back** — close the preview and return to the settings window.
* **Write separate STLs for base and features** checkbox (default on) —
  when **Create…** is pressed, writes the base and the QR + text feature
  meshes to two sibling STL files (`plate.stl` and `plate_features.stl`)
  so slicers like Bambu Studio, OrcaSlicer, and PrusaSlicer load them as
  two independently selectable bodies — assign a different filament to
  each for a two-color print. Uncheck to write a single combined STL
  instead.
* **Create…** — opens a native save dialog and writes the STL(s); the
  success dialog lists every file written.

### macOS / Homebrew note

Homebrew's Python 3.11 ships without Tk bindings by default. If
`qr23mf gui` reports `Tkinter is not available`, install the matching
Tk package:

```bash
brew install python-tk@3.11
```

Then re-run `uv sync --all-extras` (or reinstall with `uv tool install .`)
so the virtual environment picks up the refreshed interpreter.

## Development workflow

```bash
task check          # pre-commit gate: fmt check + lint + typecheck + tests w/ coverage
task fmt            # auto-format (ruff format)
task lint           # ruff check
task typecheck      # mypy --strict
task test           # pytest (no coverage)
task test:coverage  # pytest + 85% coverage gate
task build          # produce wheel + sdist
task clean          # remove caches and build artifacts
```

All tasks invoke `uv run ...` under the hood, so a matching virtual environment is created automatically.

## Roadmap

Scope vBRIEFs live in `vbrief/`. Promote proposed scopes via `task -d deft scope:promote` and activate them with `task -d deft scope:activate`. See `deft/docs/BROWNFIELD.md` for the full lifecycle.

## License

MIT
