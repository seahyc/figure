#!/usr/bin/env bash
#
# Run figure-agent-v2 against the local challenge server.
#
# Usage:
#   ./run-agent.sh                              # Full 30-step run
#   ./run-agent.sh --step 5                     # Start from step 5
#   ./run-agent.sh --type hover_reveal          # Test specific challenge type
#   ./run-agent.sh --no-obstacles               # Skip popup/modal layer
#   ./run-agent.sh --model gemini               # Use specific model
#   ./run-agent.sh --step 10 --no-obstacles     # Combine options
#
# For batch testing, use test-runner.py instead:
#   python3 test-runner.py --all                # Test all types
#   python3 test-runner.py --types click_reveal,drag_drop
#   python3 test-runner.py --retry-failed results/run_XXX.json
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/../../agents/browser-use" && pwd)"
SERVER_PORT=8765
BASE_URL="http://127.0.0.1:${SERVER_PORT}"

# Parse args
STEP=""
TYPE=""
OBSTACLES="1"
MODEL="gemini"
DEBUG=""
EXTRA_ARGS=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --step|-s) STEP="$2"; shift 2;;
    --type|-t) TYPE="$2"; shift 2;;
    --no-obstacles) OBSTACLES="0"; shift;;
    --model|-m) MODEL="$2"; shift 2;;
    --debug) DEBUG="1"; shift;;
    *) EXTRA_ARGS="$EXTRA_ARGS $1"; shift;;
  esac
done

# Build URL
URL="${BASE_URL}/"
PARAMS=""
if [[ -n "$STEP" ]]; then
  PARAMS="step=${STEP}"
elif [[ -n "$TYPE" ]]; then
  PARAMS="type=${TYPE}"
else
  PARAMS="step=1"
fi

[[ "$OBSTACLES" == "0" ]] && PARAMS="${PARAMS}&obstacles=0"
[[ -n "$DEBUG" ]] && PARAMS="${PARAMS}&debug=1"
URL="${URL}?${PARAMS}"

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
echo "Figure Agent v2 — Local Challenge"
echo "============================================"
echo "URL:        ${URL}"
echo "Model:      ${MODEL}"
echo "Obstacles:  $([ "$OBSTACLES" = "1" ] && echo "ON" || echo "OFF")"
[[ -n "$STEP" ]] && echo "Step:       ${STEP}"
[[ -n "$TYPE" ]] && echo "Type:       ${TYPE}"
echo "============================================"
echo

# Run agent
cd "$AGENT_DIR"
python3 agent.py \
  --url "${URL}" \
  --goal-file prompts/browser-challenge.txt \
  --model "${MODEL}" \
  $EXTRA_ARGS
