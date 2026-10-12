#!/usr/bin/env bash
# Load test of the search service on this PC's GPU (see scripts/load_test.py).
#
#   ./scripts/load_test.sh                                   full test, settings from .env
#   ./scripts/load_test.sh -e LLM_GPU_LAYERS=20 -- --scenarios baseline
#                                                            other GPU settings, baseline only
#   python3 scripts/load_test.py --compare loadtest_results/*   compare the runs
#
# Starts a throwaway copy of the search service on port 7870 with its own empty
# database, so real accounts and chats are never touched.  The GPU normally has
# room for one copy of the models only, so the search service is stopped during
# the test and started again afterwards (also when the test is interrupted).
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_ARGS=()
SETTINGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -e) ENV_ARGS+=(-e "$2"); SETTINGS+=("$2"); shift 2 ;;
        --) shift; break ;;
        *) break ;;
    esac
done
LABEL=$(IFS=_; printf '%s' "${SETTINGS[*]:-default}" | tr -c 'A-Za-z0-9_=.-' '_' | tr '=' '-')
PORT=${LOADTEST_PORT:-7870}
OUT=loadtest_results/$(date +%Y%m%d_%H%M%S)_${LABEL}

WAS_RUNNING=$(docker ps -q -f name='^scopus-ai-gpu$')
cleanup() {
    docker rm -f scopus-loadtest >/dev/null 2>&1 || true
    if [[ -n "$WAS_RUNNING" ]]; then
        docker start scopus-ai-gpu >/dev/null && echo "Search service started again."
    fi
}
trap cleanup EXIT

if [[ -n "$WAS_RUNNING" ]]; then
    echo "Stopping the search service for the test (the GPU has room for one copy of the models)."
    docker stop scopus-ai-gpu >/dev/null
fi

docker compose run -d --rm --no-deps --name scopus-loadtest -p "$PORT:7860" \
    -e HISTORY_DB_PATH=/tmp/loadtest.sqlite3 "${ENV_ARGS[@]}" \
    scopus-ai python -m search_service >/dev/null

echo "Waiting for the test instance on port $PORT ..."
for _ in $(seq 1 120); do
    curl -s -o /dev/null -f "http://localhost:$PORT/api/auth/state" && break
    sleep 2
done

python3 scripts/load_test.py --url "http://localhost:$PORT" --out "$OUT" \
    --label "$LABEL" --settings "${SETTINGS[*]:-}" "$@"
