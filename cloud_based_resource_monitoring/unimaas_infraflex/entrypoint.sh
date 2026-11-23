#!/usr/bin/env bash
set -euo pipefail

# If custom arguments are passed (e.g., `docker compose run unimaas-infraflex python ontology_bridge/aas_to_ontology.py`),
# execute them directly instead of starting the background daemon suite.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

echo "[entrypoint] Starting unimaas_infraflex services..."

python -u /app/unimaas_infraflex/vo_layer/vo_manager.py &
PID_VO=$!
echo "[entrypoint] Started vo_manager.py (PID ${PID_VO})"

python -u /app/unimaas_infraflex/kafka/aas_consumer.py &
PID_CONSUMER=$!
echo "[entrypoint] Started aas_consumer.py (PID ${PID_CONSUMER})"

python -u /app/unimaas_infraflex/kg/infraflex2kg.py &
PID_KG=$!
echo "[entrypoint] Started infraflex2kg.py (PID ${PID_KG})"

cleanup() {
    echo "[entrypoint] Shutting down unimaas_infraflex services..."
    kill -TERM "${PID_VO}" "${PID_CONSUMER}" "${PID_KG}" 2>/dev/null || true
    wait "${PID_VO}" "${PID_CONSUMER}" "${PID_KG}" 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

# Wait until any of the 3 processes exits; if one crashes, terminate the others
# and exit non-zero so Docker's restart policy restarts the container cleanly.
wait -n "${PID_VO}" "${PID_CONSUMER}" "${PID_KG}"
EXIT_CODE=$?
echo "[entrypoint] A service exited with code ${EXIT_CODE}. Stopping remaining services..."
kill -TERM "${PID_VO}" "${PID_CONSUMER}" "${PID_KG}" 2>/dev/null || true
wait 2>/dev/null || true
exit "${EXIT_CODE}"

