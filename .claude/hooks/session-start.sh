#!/bin/bash
set -euo pipefail

# MCP servers (see .mcp.json) reference secrets like ${SOCIALCRAWL_API_KEY}
# via shell-environment substitution at launch time. Those secrets live in
# .env (read directly by the Python engine via pydantic-settings) but never
# reach the session's own shell environment otherwise, so MCP servers start
# with an empty value and fail auth even when .env has a valid key. This
# hook closes that gap by exporting .env into $CLAUDE_ENV_FILE before MCP
# servers launch. Only needed in remote/web sessions — local dev shells
# manage their own environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ENV_FILE="${CLAUDE_PROJECT_DIR:-.}/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "session-start.sh: no .env found at $ENV_FILE, skipping" >&2
  exit 0
fi

while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ''|'#'*) continue ;;
  esac
  if [[ "$line" == *=* ]]; then
    echo "export $line" >> "$CLAUDE_ENV_FILE"
  fi
done < "$ENV_FILE"
