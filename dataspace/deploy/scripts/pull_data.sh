#!/usr/bin/env bash
# ==============================================================================
# Pull & Populate Data Script for Unified Deployment
#
# Pulls and loads:
#   1. AAS shells, submodels, and concept descriptions from
#      repos/unimaas-infraflex/AAS into BaSyx (:8081) and registers them in
#      AAS Registry (:8082) & Submodel Registry (:8083).
#   2. Loads provider RDF datasets (example_provider_data.ttl & adient_data.ttl)
#      into Provider Jena Fuseki (:3032/provider/data).
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$DEPLOY_DIR"

if [[ -f "${DEPLOY_DIR}/.env" ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source "${DEPLOY_DIR}/.env"
  set +o allexport
fi

REPOS_PATH="$(mkdir -p "${REPOS_DIR:-./repos}" && cd "${REPOS_DIR:-./repos}" && pwd)"
if [[ -z "${INFRAFLEX_DIR:-}" ]]; then
  if [[ -d "${DEPLOY_DIR}/../../cloud_based_resource_monitoring/basyx-setup" ]]; then
    INFRAFLEX_DIR="$(cd "${DEPLOY_DIR}/../../cloud_based_resource_monitoring" && pwd)"
  elif [[ -d "${DEPLOY_DIR}/../basyx-setup" ]]; then
    INFRAFLEX_DIR="$(cd "${DEPLOY_DIR}/.." && pwd)"
  else
    INFRAFLEX_DIR="$(cd "${DEPLOY_DIR}/../../cloud_based_resource_monitoring" && pwd)"
  fi
elif [[ "${INFRAFLEX_DIR}" != /* ]]; then
  INFRAFLEX_DIR="$(cd "${DEPLOY_DIR}/${INFRAFLEX_DIR}" && pwd)"
fi
DMS_DIR="${REPOS_PATH}/ids-mvd-dms"

echo "=================================================================="
echo " 1. Preparing & Loading AAS Data into BaSyx (${EXTERNAL_HOST:-localhost}:${AAS_ENV_PORT:-8081})"
echo "=================================================================="
if [[ -d "${INFRAFLEX_DIR}/AAS" ]]; then
  if [[ -d "${INFRAFLEX_DIR}/basyx-setup/aas" ]]; then
    echo "[pull_data] Syncing AAS JSON files to basyx-setup/aas..."
    for subdir in addons arms cells characteristics executors processes products routes sensors tools; do
      if [[ -d "${INFRAFLEX_DIR}/AAS/${subdir}" ]]; then
        cp -f "${INFRAFLEX_DIR}/AAS/${subdir}"/*.json "${INFRAFLEX_DIR}/basyx-setup/aas/" 2>/dev/null || true
      fi
    done
  fi

  echo "[pull_data] Uploading and registering all AAS shells & submodels in BaSyx..."
  python3 "${SCRIPT_DIR}/load_aas.py" "${INFRAFLEX_DIR}/AAS"

  echo "[pull_data] Patching aas-ui container to dynamically resolve browser hostname (${EXTERNAL_HOST:-localhost})..."
  if docker ps --format '{{.Names}}' | grep -q '^aas-ui$'; then
    docker cp "${SCRIPT_DIR}/patch_aas_ui.sh" aas-ui:/tmp/patch_aas_ui.sh
    docker exec aas-ui sh /tmp/patch_aas_ui.sh "${EXTERNAL_HOST:-localhost}"
  fi
else
  echo "[pull_data] WARNING: ${INFRAFLEX_DIR}/AAS not found. Run './deploy.sh clone' first."
fi

echo "=================================================================="
echo " 2. Loading Provider RDF Data into Jena Fuseki (:3032/provider)"
echo "=================================================================="
SHIRO_FILE="${DMS_DIR}/fuseki/provider/shiro.ini"
if [[ -f "$SHIRO_FILE" ]] && grep -q 'roles\[admin\]' "$SHIRO_FILE"; then
  echo "[pull_data] Updating Fuseki shiro.ini to allow browser UI & SPARQL access without login popup..."
  cat > "$SHIRO_FILE" <<'SHIRO'
[main]
authcBasic = org.apache.shiro.web.filter.authc.BasicHttpAuthenticationFilter

[users]
admin = mypassword, admin

[roles]
admin = *

[urls]
/** = anon
SHIRO
  docker restart fuseki-server-provider >/dev/null 2>&1 || true
fi

FUSEKI_URL="http://localhost:${FUSEKI_PROVIDER_PORT:-3032}"
echo "[pull_data] Waiting for Fuseki at ${FUSEKI_URL}/$/ping ..."
for _ in $(seq 1 20); do
  if curl -sf "${FUSEKI_URL}/\$/ping" >/dev/null 2>&1 || curl -sf -u "admin:mypassword" "${FUSEKI_URL}/provider/query?query=ASK%7B%3Fs%20%3Fp%20%3Fo%7D" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

for ttl_file in "${DMS_DIR}/fuseki/provider/example_provider_data.ttl" "${DMS_DIR}/fuseki/provider/adient_data.ttl"; do
  if [[ -f "$ttl_file" ]]; then
    echo "[pull_data] Uploading $(basename "$ttl_file") to ${FUSEKI_URL}/provider/data ..."
    curl -sf -u "admin:mypassword" \
      -X POST \
      -H "Content-Type: text/turtle" \
      --data-binary @"$ttl_file" \
      "${FUSEKI_URL}/provider/data" && echo "  -> Loaded $(basename "$ttl_file") successfully." || echo "  -> Warning: could not upload $(basename "$ttl_file")."
  fi
done

echo "=================================================================="
echo " Data pull & initialization complete!"
echo "=================================================================="
