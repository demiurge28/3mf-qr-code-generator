#!/usr/bin/env bash
# qr23mf installer.
#
# Installs qr23mf as a user-level tool via uv (preferred) or pipx, and
# optionally installs Python's Tk bindings (needed for `qr23mf gui`).
#
# Usage:
#   ./install.sh                       # interactive
#   ./install.sh --noninteractive      # auto-yes to every prompt
#   ./install.sh --skip-tk             # do not try to install Tk bindings
#   ./install.sh --tool=uv|pipx        # force a specific install tool
#   ./install.sh --help
#
# Run this script from a checkout of the qr23mf source tree (i.e. the
# directory containing pyproject.toml).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# -----------------------------------------------------------------------------
# Options
# -----------------------------------------------------------------------------

NONINTERACTIVE=0
SKIP_TK=0
INSTALL_TOOL=""

usage() {
    cat <<'EOF'
qr23mf installer

Options:
  --noninteractive   Auto-confirm every prompt
  --skip-tk          Do not install Python Tk bindings
  --tool=uv|pipx     Force the install tool (default: uv if present, else pipx)
  -h, --help         Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --noninteractive) NONINTERACTIVE=1 ;;
        --skip-tk)        SKIP_TK=1 ;;
        --tool=uv|--tool=pipx) INSTALL_TOOL="${1#--tool=}" ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# -----------------------------------------------------------------------------
# Pretty printing
# -----------------------------------------------------------------------------

cyan()   { printf '\033[0;36m%s\033[0m\n' "$*"; }
green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
red()    { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }
die()    { red "Error: $*"; exit 1; }

confirm() {
    local prompt="${1:-Continue?}"
    if [ "$NONINTERACTIVE" -eq 1 ]; then
        return 0
    fi
    local reply
    read -r -p "$prompt [Y/n] " reply
    case "${reply:-Y}" in
        Y|y|yes|Yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

# -----------------------------------------------------------------------------
# Preflight: source layout and OS
# -----------------------------------------------------------------------------

if [ ! -f "$SCRIPT_DIR/pyproject.toml" ]; then
    die "pyproject.toml not found in $SCRIPT_DIR. Run this script from the qr23mf source tree."
fi

case "$(uname -s)" in
    Darwin) OS="macos" ;;
    Linux)  OS="linux" ;;
    *)      OS="other" ;;
esac

cyan "==> qr23mf installer"
cyan "    source: $SCRIPT_DIR"
cyan "    host:   $OS"

# -----------------------------------------------------------------------------
# Python >= 3.11
# -----------------------------------------------------------------------------

if ! command -v python3 >/dev/null 2>&1; then
    die "python3 not found on PATH. Install Python 3.11+ first."
fi

PY_VERSION="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
PY_MAJOR="${PY_VERSION%%.*}"
PY_TMP="${PY_VERSION#*.}"
PY_MINOR="${PY_TMP%%.*}"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    die "Python 3.11 or newer is required (found $PY_VERSION)."
fi
cyan "    python: $PY_VERSION"

# -----------------------------------------------------------------------------
# Pick install tool (uv preferred, pipx fallback, offer to install uv)
# -----------------------------------------------------------------------------

pick_tool() {
    if [ -n "$INSTALL_TOOL" ]; then
        command -v "$INSTALL_TOOL" >/dev/null 2>&1 \
            || die "$INSTALL_TOOL requested via --tool but not found on PATH."
        printf '%s' "$INSTALL_TOOL"
        return
    fi
    if command -v uv   >/dev/null 2>&1; then printf 'uv';   return; fi
    if command -v pipx >/dev/null 2>&1; then printf 'pipx'; return; fi

    yellow "Neither uv nor pipx found on PATH."
    if confirm "Install uv (https://docs.astral.sh/uv/) now?"; then
        # Official Astral installer. Review https://astral.sh/uv/install.sh
        # before running if you want to vet it.
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        command -v uv >/dev/null 2>&1 \
            || die "uv install did not put uv on PATH. Re-open your shell and re-run."
        printf 'uv'
        return
    fi
    die "No installer available. Install uv or pipx and re-run this script."
}

TOOL="$(pick_tool)"
cyan "    install tool: $TOOL"

# -----------------------------------------------------------------------------
# Tk bindings (needed for `qr23mf gui`)
# -----------------------------------------------------------------------------

install_tk_macos() {
    if ! command -v brew >/dev/null 2>&1; then
        yellow "Homebrew not found; cannot install Tk automatically on macOS."
        yellow "Install Homebrew from https://brew.sh or re-run with --skip-tk."
        return 1
    fi
    local pkg="python-tk@${PY_MAJOR}.${PY_MINOR}"
    if brew list --formula 2>/dev/null | grep -qx "$pkg"; then
        cyan "    tk: $pkg already installed"
        return 0
    fi
    if confirm "Install $pkg via Homebrew?"; then
        brew install "$pkg"
        return $?
    fi
    return 1
}

install_tk_linux() {
    if command -v apt-get >/dev/null 2>&1; then
        if confirm "Install python3-tk via apt-get (requires sudo)?"; then
            sudo apt-get update && sudo apt-get install -y python3-tk
            return $?
        fi
    elif command -v dnf >/dev/null 2>&1; then
        if confirm "Install python3-tkinter via dnf (requires sudo)?"; then
            sudo dnf install -y python3-tkinter
            return $?
        fi
    elif command -v pacman >/dev/null 2>&1; then
        if confirm "Install tk via pacman (requires sudo)?"; then
            sudo pacman -S --noconfirm tk
            return $?
        fi
    else
        yellow "Unknown package manager; please install Python Tk bindings manually."
    fi
    return 1
}

if [ "$SKIP_TK" -ne 1 ]; then
    if python3 -c "import tkinter" >/dev/null 2>&1; then
        cyan "    tk: already available"
    else
        yellow "Tkinter is not available in python3. 'qr23mf gui' needs it."
        case "$OS" in
            macos) install_tk_macos || yellow "    Skipped; 'qr23mf gui' unavailable until Tk is installed." ;;
            linux) install_tk_linux || yellow "    Skipped; 'qr23mf gui' unavailable until Tk is installed." ;;
            *)     yellow "    Unknown OS; install Tk bindings manually." ;;
        esac
    fi
fi

# -----------------------------------------------------------------------------
# Install qr23mf from the source tree
# -----------------------------------------------------------------------------

cyan "==> Installing qr23mf from $SCRIPT_DIR via $TOOL"
case "$TOOL" in
    uv)   uv tool install --force --reinstall . ;;
    pipx) pipx install --force . ;;
esac

# -----------------------------------------------------------------------------
# Verify
# -----------------------------------------------------------------------------

if command -v qr23mf >/dev/null 2>&1; then
    green "==> Installed: $(qr23mf --version)"
    green "    Try:  qr23mf generate --text 'https://example.com' --out coaster.stl"
    green "    Or:   qr23mf gui"
else
    yellow "qr23mf installed but not on PATH yet. You may need to update your shell:"
    case "$TOOL" in
        uv)   yellow "    Run:  uv tool update-shell     # then re-open the shell" ;;
        pipx) yellow "    Run:  pipx ensurepath          # then re-open the shell" ;;
    esac
    exit 1
fi
