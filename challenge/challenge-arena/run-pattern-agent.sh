#!/usr/bin/env bash
#
# Run pattern-agent against the local challenge server.
#
# Usage:
#   ./run-pattern-agent.sh                          # Full 30-step run
#   ./run-pattern-agent.sh --step 5                 # Start from step 5
#   ./run-pattern-agent.sh --type hover_reveal      # Test specific challenge type
#   ./run-pattern-agent.sh --no-obstacles           # Skip popup/modal layer
#   ./run-pattern-agent.sh --headless               # Headless browser
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/../../agents/pattern-agent" && pwd)"
SERVER_PORT=8765
BASE_URL="http://127.0.0.1:${SERVER_PORT}"

# Parse args
STEP=""
TYPE=""
OBSTACLES="1"
HEADLESS="0"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    --step|-s) STEP="$2"; shift 2;;
    --type|-t) TYPE="$2"; shift 2;;
    --no-obstacles) OBSTACLES="0"; shift;;
    --headless) HEADLESS="1"; shift;;
    *) EXTRA_ARGS+=("$1"); shift;;
  esac
done

# Build URL
PARAMS=""
if [[ -n "$STEP" ]]; then
  PARAMS="step=${STEP}"
elif [[ -n "$TYPE" ]]; then
  PARAMS="type=${TYPE}"
else
  PARAMS="step=1"
fi
[[ "$OBSTACLES" == "0" ]] && PARAMS="${PARAMS}&obstacles=0"
URL="${BASE_URL}/?${PARAMS}"

# Check server is running
if ! curl -s "${BASE_URL}/api/config" > /dev/null 2>&1; then
  echo "Starting local challenge server on port ${SERVER_PORT}..."
  python3 "${SCRIPT_DIR}/server.py" --port "${SERVER_PORT}" &
  SERVER_PID=$!
  sleep 1
  trap "kill $SERVER_PID 2>/dev/null" EXIT
  echo "Server started (PID: ${SERVER_PID})"
fi

echo "============================================"
echo "Pattern Agent — Local Challenge"
echo "============================================"
echo "URL:        ${URL}"
echo "Obstacles:  $([ "$OBSTACLES" = "1" ] && echo "ON" || echo "OFF")"
[[ -n "$STEP" ]] && echo "Step:       ${STEP}"
[[ -n "$TYPE" ]] && echo "Type:       ${TYPE}"
echo "Headless:   $([ "$HEADLESS" = "1" ] && echo "YES" || echo "NO")"
echo "============================================"
echo

cd "$AGENT_DIR"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"

ARGS=(--url "${URL}")
[[ "$HEADLESS" == "1" ]] && ARGS+=(--headless)

uv run python -m pattern_agent "${ARGS[@]}" "${EXTRA_ARGS[@]}"
