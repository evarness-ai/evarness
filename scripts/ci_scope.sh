#!/usr/bin/env bash
# Whether a pull request can affect the `webui` CI job (the `changes` job in ci.yml).
#
#   webui -> the `webui` job (npm install, lint, typecheck, build, Playwright). Runs when
#            webui/, the workflows or the bundling script changed.
#
# A job skipped by its own `if:` still reports a status, and GitHub counts a skipped job
# as passing for branch protection. A `paths:` filter on the workflow would not: the
# required check would never be reported. Every other check runs on every PR.
#
# It fails open: anything but a pull request, a base it cannot find, an empty diff or an
# error means webui=true, so the job runs.
#
# Usage:  EVENT_NAME=pull_request PR_BASE_SHA=<sha> scripts/ci_scope.sh
#         CHANGED_OVERRIDE=$'a.md\nb.py' scripts/ci_scope.sh      (tests: bypass git)
# Prints  webui=<true|false>; appends it to $GITHUB_OUTPUT.
set -euo pipefail

webui=true

webui_paths='^(webui/|\.github/workflows/|scripts/bundle_webui\.py$)'

files=""
if [[ -n "${CHANGED_OVERRIDE+x}" ]]; then
  files="$CHANGED_OVERRIDE"
elif [[ "${EVENT_NAME:-}" == "pull_request" && -n "${PR_BASE_SHA:-}" ]] \
  && git cat-file -e "${PR_BASE_SHA}^{commit}" 2>/dev/null; then
  files=$(git diff --name-only "$PR_BASE_SHA" HEAD 2>/dev/null || true)
fi

if [[ -n "$files" ]]; then
  grep -qE "$webui_paths" <<<"$files" || webui=false
fi

echo "webui=$webui"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "webui=$webui" >> "$GITHUB_OUTPUT"
fi
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### Job scope"
    echo
    echo "- webui job: $([[ $webui == true ]] && echo runs || echo skipped, no webui, workflow or bundling change)"
    echo
    echo "Every other check ran in full."
  } >> "$GITHUB_STEP_SUMMARY"
fi
