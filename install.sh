#!/usr/bin/env bash
# Bootstrap a clone of this repo: find a supported Python, run `poetry install`, then
# hand off to `evarness setup` for everything after that (home dir, data dir, secret,
# services, Telegram, email). To try Evarness rather than work on it, skip the clone:
# `uv tool install "evarness[email]" --upgrade` (docs: getting-started/install). `evarness setup` itself needs Poetry's venv to already exist, so this one
# step can't live inside it -- that's the whole reason this script exists.
#
# Usage: ./install.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "checking Python..."
# Evarness runs on Python 3.12 or 3.13 (pyproject's requires-python). `python3` on a Mac
# is often Homebrew's newest, so a supported interpreter is looked for by name as well,
# and Poetry is pointed at the one found.
PYTHON=""
for candidate in python3.12 python3.13 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$("$candidate" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"
    if [[ "$version" == "3.12" || "$version" == "3.13" ]]; then
      PYTHON="$candidate"
      PY_VERSION="$version"
      break
    fi
    found="$candidate is $version"
  fi
done
if [[ -z "$PYTHON" ]]; then
  echo "error: Python 3.12 or 3.13 is required; ${found:-no python3 found}." >&2
  echo "  Install one (python.org, Homebrew 'brew install python@3.13', pyenv, or 'uv python install 3.13')," >&2
  echo "  then re-run ./install.sh. Testing rather than developing? Skip the clone:" >&2
  echo "    uv tool install \"evarness[email]\" --upgrade" >&2
  exit 1
fi
echo "  ok ($PYTHON, $PY_VERSION)"

echo "checking Poetry..."
if ! command -v poetry >/dev/null 2>&1; then
  cat >&2 <<'EOF'
error: Poetry not found.

Install it yourself first (this script won't pipe a remote installer for you):
  curl -sSL https://install.python-poetry.org | python3 -

Then re-run ./install.sh.
EOF
  exit 1
fi
echo "  ok ($(poetry --version))"

echo "installing dependencies (poetry install)..."
poetry env use "$PYTHON" >/dev/null
poetry install --extras email

echo "handing off to the setup wizard..."
echo
exec poetry run evarness setup
