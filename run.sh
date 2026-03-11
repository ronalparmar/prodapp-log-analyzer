#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run.sh — ProdApp Log Analyzer  |  one-shot setup + launch
# Usage:  ./run.sh [--port 8000] [--host 0.0.0.0]
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
REQUIREMENTS="$SCRIPT_DIR/backend/requirements.txt"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

# Parse optional flags
while [[ $# -gt 0 ]]; do
  case $1 in
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
die()     { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

header "━━━  ProdApp Log Analyzer — Setup & Run  ━━━"

# ── 1. Python ────────────────────────────────────────────────────────────────
header "1/4  Checking Python"
PYTHON=""
for cmd in python3 python3.12 python3.11 python3.10 python3.9 python; do
  if command -v "$cmd" &>/dev/null; then
    ver=$("$cmd" -c "import sys; print('%d.%d' % sys.version_info[:2])")
    major=${ver%%.*}; minor=${ver##*.}
    if [[ $major -ge 3 && $minor -ge 9 ]]; then
      PYTHON=$(command -v "$cmd")
      ok "Found $PYTHON  (Python $ver)"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  warn "Python 3.9+ not found — attempting install via Homebrew (macOS) or apt (Linux)"
  if command -v brew &>/dev/null; then
    brew install python@3.11
    PYTHON=$(command -v python3.11) || die "Python install failed. Install Python 3.9+ manually."
  elif command -v apt-get &>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y python3 python3-pip python3-venv
    PYTHON=$(command -v python3) || die "Python install failed. Install Python 3.9+ manually."
  else
    die "Cannot install Python automatically. Please install Python 3.9+ from https://www.python.org/downloads/"
  fi
  ok "Python installed: $PYTHON"
fi

# ── 2. Virtual environment ───────────────────────────────────────────────────
header "2/4  Virtual environment"
if [[ ! -d "$VENV_DIR" ]]; then
  info "Creating venv at $VENV_DIR …"
  "$PYTHON" -m venv "$VENV_DIR" || die "Failed to create virtual environment."
  ok "Virtual environment created"
else
  ok "Virtual environment already exists"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# Upgrade pip silently
"$VENV_PYTHON" -m pip install --upgrade pip --quiet

# ── 3. Dependencies ──────────────────────────────────────────────────────────
header "3/4  Installing dependencies"
[[ -f "$REQUIREMENTS" ]] || die "requirements.txt not found at $REQUIREMENTS"

# Check if all packages are already satisfied to avoid re-installing every run
if "$VENV_PIP" install -r "$REQUIREMENTS" --quiet 2>&1 | grep -qE "^(Successfully installed|Collecting)"; then
  ok "Dependencies installed / updated"
else
  ok "All dependencies already satisfied"
fi

# ── 4. Launch ────────────────────────────────────────────────────────────────
header "4/4  Starting server"

# Free the port if something is already bound to it
if lsof -ti:"$PORT" &>/dev/null; then
  warn "Port $PORT is in use — stopping existing process"
  lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
  sleep 1
fi

echo -e "\n${BOLD}${GREEN}✓ App is starting at http://${HOST}:${PORT}${RESET}"
echo -e "  Press ${BOLD}Ctrl+C${RESET} to stop.\n"

cd "$SCRIPT_DIR"
PYTHONPATH=backend exec "$VENV_PYTHON" -m uvicorn app.main:app \
  --reload \
  --host "$HOST" \
  --port "$PORT"
