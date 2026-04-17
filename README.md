# qr2stl

Turn a string or URL into a 3D-printable QR code mesh: single-color **STL** or two-color **3MF** with per-body color metadata. Built as a small, pure-Python CLI.

> **Status:** bootstrap skeleton only. Functionality lands scope-by-scope — see `vbrief/proposed/` and `vbrief/active/` for the work-in-progress plan.

## Install (development)

Requires [uv](https://docs.astral.sh/uv/) and optionally [Task](https://taskfile.dev/).

```bash
uv sync --all-extras
uv run qr2stl --help
```

## Install (end users)

```bash
uv tool install .
# or
pipx install .
```

Either command exposes a `qr2stl` binary on `PATH`.

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
