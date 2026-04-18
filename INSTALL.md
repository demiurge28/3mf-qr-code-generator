# Installing qr23mf

`qr23mf` is a small pure-Python CLI + Tkinter GUI that turns a string or URL
into a 3D-printable QR code as a **two-object 3MF** — a standard 3MF
package containing the base plate and the QR / text features as two
independently selectable bodies, so any modern slicer can assign a
different filament to each for a two-color print.

## Prerequisites

- **Python 3.11, 3.12, or 3.13**
- **[uv](https://docs.astral.sh/uv/)** (recommended) or **pipx** for
  end-user installs
- *(Optional)* **[Task](https://taskfile.dev/)** if you plan to use the
  project's development tasks

## End-user install

Pick one:

```bash
uv tool install .
```

or

```bash
pipx install .
```

Either command exposes a `qr23mf` binary on your `PATH`.

```bash
qr23mf --help
qr23mf generate --text "https://example.com" --out coaster.stl
```

## Development install

Clone the repo, then from the project root:

```bash
uv sync --all-extras
uv run qr23mf --help
```

This creates a local virtual environment in `.venv/` and installs all
runtime and development dependencies (ruff, mypy, pytest, coverage).

### Running the pre-commit gate

```bash
task check          # fmt:check + lint + typecheck + tests w/ 85% coverage
task fmt            # auto-format (ruff format)
task lint           # ruff check
task typecheck      # mypy --strict
task test           # pytest (no coverage)
task test:coverage  # pytest with 85% coverage gate
```

All tasks invoke `uv run ...` under the hood, so they pick up the project
virtual environment automatically.

## GUI prerequisites (Tkinter)

The `qr23mf gui` subcommand requires Python's Tk bindings. These ship with
many Python distributions but are missing from some — notably Homebrew's
Python on macOS.

### macOS (Homebrew)

```bash
brew install python-tk@3.11    # or @3.12 / @3.13 to match your Python
```

If `qr23mf gui` reports `Tkinter is not available`, rebuild the virtual
environment afterwards so it picks up the refreshed interpreter:

```bash
uv sync --all-extras
```

### Debian / Ubuntu

```bash
sudo apt-get install python3-tk
```

### Fedora / RHEL

```bash
sudo dnf install python3-tkinter
```

### Windows

The official python.org installer bundles Tk by default — no extra step is
needed. On stripped-down distributions, install the "tcl/tk and IDLE"
optional feature in the Python installer.

## Verifying your install

```bash
qr23mf --version                                         # prints the qr23mf version
qr23mf generate --text "qr23mf install ok"               # prints a mesh summary
qr23mf generate --text "https://example.com" \
  --out coaster.3mf                                      # writes a two-object 3MF
qr23mf gui                                               # launches the GUI (needs Tk)
```

Load `coaster.3mf` into Bambu Studio, OrcaSlicer, or PrusaSlicer — you
should see two parts (the base and the QR / text features) and be able
to assign a separate filament to each.

## Uninstalling

```bash
uv tool uninstall 3mf-qr-code-generator   # if installed via uv tool
pipx uninstall 3mf-qr-code-generator      # if installed via pipx
```

## Troubleshooting

- **`qr23mf: command not found`** — ensure your tool-install bin directory
  is on `PATH` (`uv tool install` prints the location; `pipx ensurepath`
  fixes it for pipx).
- **`Tkinter is not available in this Python install`** — install the Tk
  bindings for your platform (see [GUI prerequisites](#gui-prerequisites-tkinter))
  and re-run `uv sync --all-extras` if you're using the dev environment.
- **`Pillow is required for the GUI`** — run `uv sync --all-extras` (or
  `pip install pillow`) to install it. Pillow is a required runtime
  dependency; this error only appears if the install was partial.
