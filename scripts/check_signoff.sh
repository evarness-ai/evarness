#!/usr/bin/env bash
# commit-msg hook: every commit carries a Signed-off-by line (DCO; CONTRIBUTING.md).
# pre-commit passes the message file as $1. `git commit -s` adds the line.
set -euo pipefail
msg_file="${1:?commit message file}"
if grep -qE '^Signed-off-by: .+ <.+@.+>$' "$msg_file"; then
  exit 0
fi
echo "commit-msg: no Signed-off-by line. Commit with \`git commit -s\` (DCO, see CONTRIBUTING.md)." >&2
exit 1
