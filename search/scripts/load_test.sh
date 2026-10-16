#!/usr/bin/env bash
# Load test of the search service on this PC's GPU (see scripts/load_test.py).
#
#   ./scripts/load_test.sh                                   full test, settings from .env
#   ./scripts/load_test.sh -e LLM_PARALLEL=4                 4 answers at the same time
#   ./scripts/load_test.sh -e SAFETY_OUTPUT=false -- --scenarios baseline
#                                                            other settings, baseline only
#   python3 scripts/load_test.py --compare loadtest_results/*   compare the runs
#
# Starts a throwaway copy of the search service on port 7870 with its own empty
# database, so real accounts and chats are never touched.  The normal search
# service is stopped during the test (GPU memory) and started again afterwards,
# also when the test is interrupted.  LLM_* settings (LLM_PARALLEL, LLM_CTX,
# LLM_MODEL, ...) also restart the language model server with them; it gets its
# .env settings back at the end.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_ARGS=()
SETTINGS=()
LLM_ENV=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -e) ENV_ARGS+=(-e "$2"); SETTINGS+=("$2")
            [[ "$2" == LLM_* ]] && LLM_ENV+=("$2")
            shift 2 ;;
        --) shift; break ;;
        *) break ;;
    esac
done
LABEL=$(IFS=_; printf '%s' "${SETTINGS[*]:-default}" | tr -c 'A-Za-z0-9_=.-' '_' | tr '=' '-')
PORT=${LOADTEST_PORT:-7870}
OUT=loadtest_results/$(date +%Y%m%d_%H%M%S)_${LABEL}

wait_for_llm() {
    for _ in $(seq 1 90); do
        [[ "$(docker inspect -f '{{.State.Health.Status}}' scopus-llm 2>/dev/null)" == healthy ]] && return 0
        sleep 4
    done
    echo "The language model server did not become ready." >&2
    return 1
}

WAS_RUNNING=$(docker ps -q -f name='^scopus-ai-gpu$')
cleanup() {
    docker rm -f scopus-loadtest >/dev/null 2>&1 || true
    if [[ ${#LLM_ENV[@]} -gt 0 ]]; then
        echo "Restarting the language model server with the .env settings."
        docker compose up -d --no-deps --force-recreate llm >/dev/null 2>&1 && wait_for_llm || true
    fi
    if [[ -n "$WAS_RUNNING" ]]; then
        docker start scopus-ai-gpu >/dev/null && echo "Search service started again."
    fi
}
trap cleanup EXIT

if [[ -n "$WAS_RUNNING" ]]; then
    echo "Stopping the search service for the test (GPU memory)."
    docker stop scopus-ai-gpu >/dev/null
fi
if [[ ${#LLM_ENV[@]} -gt 0 ]]; then
    echo "Restarting the language model server with ${LLM_ENV[*]}."
    env "${LLM_ENV[@]}" docker compose up -d --no-deps --force-recreate llm >/dev/null 2>&1
fi
docker compose up -d --no-deps llm >/dev/null 2>&1
wait_for_llm

docker compose run -d --rm --no-deps --name scopus-loadtest -p "$PORT:7860" \
    -e HISTORY_DB_PATH=/tmp/loadtest.sqlite3 "${ENV_ARGS[@]}" \
    scopus-ai python -m search_service >/dev/null

echo "Waiting for the test instance on port $PORT ..."
for _ in $(seq 1 150); do
    curl -s -o /dev/null -f "http://localhost:$PORT/api/health" && break
    sleep 2
done

PARALLEL=$(printf '%s\n' "${SETTINGS[@]:-}" | sed -n 's/^LLM_PARALLEL=//p' | tail -1)
PARALLEL=${PARALLEL:-$(sed -n 's/^LLM_PARALLEL=//p' .env 2>/dev/null | tail -1)}
python3 scripts/load_test.py --url "http://localhost:$PORT" --out "$OUT" \
    --label "$LABEL" --settings "${SETTINGS[*]:-}" --parallel "${PARALLEL:-1}" "$@"
