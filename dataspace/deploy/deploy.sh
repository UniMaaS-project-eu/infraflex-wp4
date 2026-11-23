#!/usr/bin/env bash
# ==============================================================================
# Unified Deployment Script for Infraflex, Central-KG, ED-Node, and IDS-MVD-DMS
#
# Usage:
#   ./deploy.sh [command]
#
# Commands:
#   all (default)  Clone repos, prepare configs/keys, build images, start stack, init DBs & pull data
#   clone          Clone or update all 4 repositories + git submodules into ./repos
#   prepare        Generate .env, DID documents, Ed25519 keys, Vault & Nginx configs (connector1, connector2, connectorcp)
#   build          Build all Docker images in the unified docker-compose.yml
#   up             Start all services in detached mode
#   init           Initialize GraphDB repos, Postgres tracking table, and pull AAS/InfraPlay/Fuseki data
#   pull-data      Pull & populate AAS shells/submodels, InfraPlay scenarios, ExecutionLogs, and Fuseki RDF
#   down           Stop and remove containers
#   down-v         Stop and remove containers and volumes
#   status         Show status of all containers in the unified stack
#   logs [service] Tail logs of all services (or a specific service)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="${SCRIPT_DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[deploy] Creating .env from .env.example..."
  cp "${SCRIPT_DIR}/.env.example" "$ENV_FILE"
fi

set -o allexport
# shellcheck disable=SC1090
source "$ENV_FILE"
set +o allexport

REPOS_PATH="$(mkdir -p "${REPOS_DIR:-./repos}" && cd "${REPOS_DIR:-./repos}" && pwd)"

init_submodules_resilient() {
  local repo_dir="$1"
  local skip_submodule="${2:-}"

  if [[ ! -f "${repo_dir}/.gitmodules" ]]; then
    return 0
  fi

  git -C "$repo_dir" submodule init

  local sub_keys
  sub_keys=$(git -C "$repo_dir" config --file .gitmodules --get-regexp '\.path$' | awk '{print $1}')

  for sk in $sub_keys; do
    local sp
    sp=$(git -C "$repo_dir" config --file .gitmodules --get "$sk")
    local url_key="${sk%.path}.url"
    local sub_url
    sub_url=$(git -C "$repo_dir" config --file .gitmodules --get "$url_key")

    if [[ -n "$skip_submodule" && "$sp" == "$skip_submodule" ]]; then
      echo "[clone] Skipping redundant submodule '${sp}' (already cloned at top level)..."
      continue
    fi

    echo "[clone] Initializing submodule '${sp}'..."
    if ! git -C "$repo_dir" submodule update --init --recursive "$sp"; then
      echo "[clone] Pinned commit not found on remote for '${sp}'; falling back to cloning default branch from ${sub_url}..."
      if ! git -C "$repo_dir" submodule update --init --remote "$sp"; then
        rm -rf "${repo_dir}/${sp}"
        git clone --recurse-submodules "$sub_url" "${repo_dir}/${sp}"
      fi
    fi

    if [[ -z "$(ls -A "${repo_dir}/${sp}" 2>/dev/null | grep -v '^\.git$' || true)" ]]; then
      echo "[clone] Submodule '${sp}' working tree is empty; cloning default branch from ${sub_url}..."
      rm -rf "${repo_dir}/${sp}"
      git clone --recurse-submodules "$sub_url" "${repo_dir}/${sp}"
    fi
  done
}

clone_or_update_repo() {
  local name="$1"
  local url="$2"
  local branch="$3"
  local skip_sub="${4:-}"
  local target_dir="${REPOS_PATH}/${name}"

  if [[ -d "${target_dir}/.git" ]]; then
    echo "[clone] Updating existing repository '${name}' (${branch})..."
    git -C "$target_dir" fetch origin
    git -C "$target_dir" checkout "$branch"
    git -C "$target_dir" pull --ff-only origin "$branch" || true
  else
    echo "[clone] Cloning '${name}' from ${url} (branch: ${branch})..."
    rm -rf "$target_dir"
    git clone --branch "$branch" "$url" "$target_dir"
  fi

  echo "[clone] Ensuring submodules are initialized in '${name}'..."
  init_submodules_resilient "$target_dir" "$skip_sub"
}

cmd_clone() {
  echo "=================================================================="
  echo " Step 1: Cloning / updating repositories in ${REPOS_PATH}"
  echo "=================================================================="
  clone_or_update_repo "unimaas-infraflex" "${REPO_INFRAFLEX_URL}" "${REPO_INFRAFLEX_BRANCH}"
  clone_or_update_repo "central-kg" "${REPO_CENTRAL_KG_URL}" "${REPO_CENTRAL_KG_BRANCH}"
  clone_or_update_repo "ed-node" "${REPO_ED_NODE_URL}" "${REPO_ED_NODE_BRANCH}"
  clone_or_update_repo "ids-mvd-dms" "${REPO_DMS_URL}" "${REPO_DMS_BRANCH}" "central-kg"
  echo "[clone] All repositories ready in ${REPOS_PATH}."
}

cmd_prepare() {
  echo "=================================================================="
  echo " Step 2: Preparing keys, DIDs, Vault configs, and Nginx configs"
  echo "=================================================================="
  local ed_node_dir="${REPOS_PATH}/ed-node"
  local issuer_dir="${ed_node_dir}/tests/issuerservice-vc"

  if [[ ! -d "$ed_node_dir" ]]; then
    echo "Error: ${ed_node_dir} not found. Run './deploy.sh clone' first."
    exit 1
  fi

  # Auto-detect external host IP if EXTERNAL_HOST is still set to 'localhost'
  if [[ "${EXTERNAL_HOST:-localhost}" == "localhost" ]]; then
    local detected_ip
    detected_ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    if [[ -n "$detected_ip" && "$detected_ip" != "127."* ]]; then
      echo "[prepare] Auto-detected host IP '${detected_ip}' for browser-facing services (EXTERNAL_HOST)."
      sed -i "s/^EXTERNAL_HOST=localhost/EXTERNAL_HOST=${detected_ip}/" "$ENV_FILE" || true
      export EXTERNAL_HOST="$detected_ip"
    fi
  fi

  # 1. Generate ED-Node keys, DID documents, Vault configs, and Nginx configs for all 3 instances:
  #    connector1 (:9876), connector2 (:9877), connectorcp (:9878)
  for instance in connector1 connector2 "${ED_NODE_INSTANCE_NAME:-connectorcp}"; do
    local did_host_port="edc-proxy-${instance}:80"

    if [[ ! -f "${ed_node_dir}/deployment/assets/keysPrivPub/${instance}/${instance}_private.pem" ]]; then
      echo "[prepare] Generating Ed25519 keys, DID document, and Vault config for '${instance}'..."
      (
        cd "$ed_node_dir"
        chmod +x generateEDNodeFiles-KeysDIDVault.sh
        ./generateEDNodeFiles-KeysDIDVault.sh "$instance" "http://${did_host_port}"
      )
    else
      echo "[prepare] Keys and DID for '${instance}' already exist, skipping key generation."
    fi

    echo "[prepare] Rendering Nginx config for '${instance}'..."
    ED_NODE_INSTANCE_NAME="$instance" envsubst '$ED_NODE_INSTANCE_NAME' \
      < "${ed_node_dir}/deployment/nginx/nginx-template.conf" \
      > "${ed_node_dir}/deployment/nginx/nginx-${instance}.conf"
  done

  # 2. Generate IssuerService-VC keys, DID, and Nginx config
  local issuer_instance="${ISSUERSERVICEVC_INSTANCE_NAME:-issuerservicevc}"
  local issuer_host_port="${UNIMAAS_ISSUERSERVICEVC_HOST:-edc-proxy-issuerservicevc}:${UNIMAAS_ISSUERSERVICEVC_DID_PORT:-80}"

  if [[ ! -f "${issuer_dir}/assets/keysPrivPub/${issuer_instance}/${issuer_instance}_private.pem" ]]; then
    echo "[prepare] Generating Ed25519 keys and DID document for '${issuer_instance}'..."
    (
      cd "$issuer_dir"
      chmod +x generateIssuerFiles-KeysDIDVault.sh
      ./generateIssuerFiles-KeysDIDVault.sh "$issuer_instance" "http://${issuer_host_port}"
    )
  else
    echo "[prepare] Keys and DID for '${issuer_instance}' already exist, skipping key generation."
  fi

  echo "[prepare] Rendering Nginx config for '${issuer_instance}'..."
  ISSUERSERVICEVC_INSTANCE_NAME="$issuer_instance" envsubst '$ISSUERSERVICEVC_INSTANCE_NAME' \
    < "${issuer_dir}/nginx/nginx-template.conf" \
    > "${issuer_dir}/nginx/nginx-${issuer_instance}.conf"

  chmod +x "${SCRIPT_DIR}/scripts/"*.sh "${SCRIPT_DIR}/scripts/"*.py 2>/dev/null || true

  echo "[prepare] Preparation complete."
}

cmd_build() {
  echo "=================================================================="
  echo " Step 3: Building Docker images"
  echo "=================================================================="
  docker compose --env-file "$ENV_FILE" -f "${SCRIPT_DIR}/docker-compose.yml" build
}

cmd_up() {
  echo "=================================================================="
  echo " Step 4: Starting unified Docker Compose stack"
  echo "=================================================================="
  docker compose --env-file "$ENV_FILE" -f "${SCRIPT_DIR}/docker-compose.yml" up -d
}

cmd_pull_data() {
  bash "${SCRIPT_DIR}/scripts/pull_data.sh"
}

cmd_init() {
  echo "=================================================================="
  echo " Step 5: Initializing GraphDB, Postgres & Pulling Application Data"
  echo "=================================================================="

  local central_kg_dir="${REPOS_PATH}/central-kg"
  local dms_dir="${REPOS_PATH}/ids-mvd-dms"

  # 1. Wait for central-platform-postgres and create inserted_assets table
  echo "[init] Waiting for central-platform-postgres to be ready..."
  until docker exec central-platform-postgres pg_isready -U "${POSTGRES_USER:-admin}" -d "${POSTGRES_DB:-postgrescp}" >/dev/null 2>&1; do
    sleep 2
  done

  local sql_file="${dms_dir}/launchers/digital-thread/postgres/table_creation.sql"
  if [[ -f "$sql_file" ]]; then
    echo "[init] Creating 'inserted_assets' table in central-platform-postgres..."
    docker exec -i central-platform-postgres \
      psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-admin}" -d "${POSTGRES_DB:-postgrescp}" < "$sql_file" || true
  else
    echo "[init] Creating 'inserted_assets' table (inline fallback)..."
    docker exec -i central-platform-postgres \
      psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-admin}" -d "${POSTGRES_DB:-postgrescp}" <<'SQL'
CREATE TABLE IF NOT EXISTS inserted_assets (
    id SERIAL PRIMARY KEY,
    asset_id VARCHAR(255) NOT NULL,
    provider_did VARCHAR(255),
    transfer_id VARCHAR(255),
    negotiation_id VARCHAR(255),
    agreement_id VARCHAR(255),
    properties JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
SQL
  fi

  # 2. Wait for central-platform-graphdb and initialize centralKG repository
  local gdb_url="http://localhost:${CENTRAL_GRAPHDB_PORT:-7200}"
  echo "[init] Waiting for central-platform-graphdb at ${gdb_url}..."
  until curl -sf "${gdb_url}/protocol" >/dev/null 2>&1; do
    sleep 3
  done

  local repo_config="${central_kg_dir}/graphdb/config/repo-config.ttl"
  if [[ -f "$repo_config" ]]; then
    echo "[init] Initializing '${CENTRAL_GRAPHDB_REPO:-centralKG}' repository in central-platform-graphdb..."
    curl -sS -u "${GRAPHDB_ADMIN_USER:-admin}:${GRAPHDB_ADMIN_PASSWORD:-mypassword}" \
      -F "config=@${repo_config}" \
      "${gdb_url}/rest/repositories" || true
  fi

  # 3. Pull AAS shells/submodels, InfraPlay scenarios, TimescaleDB execution logs, and Fuseki RDF
  cmd_pull_data

  echo "[init] Initialization finished."
}

cmd_down() {
  docker compose --env-file "$ENV_FILE" -f "${SCRIPT_DIR}/docker-compose.yml" down
}

cmd_down_v() {
  docker compose --env-file "$ENV_FILE" -f "${SCRIPT_DIR}/docker-compose.yml" down -v
}

cmd_status() {
  docker compose --env-file "$ENV_FILE" -f "${SCRIPT_DIR}/docker-compose.yml" ps
}

cmd_logs() {
  shift || true
  docker compose --env-file "$ENV_FILE" -f "${SCRIPT_DIR}/docker-compose.yml" logs -f "$@"
}

ACTION="${1:-all}"
case "$ACTION" in
  all)
    cmd_clone
    cmd_prepare
    cmd_build
    cmd_up
    cmd_init
    echo "=================================================================="
    echo " Unified deployment is up and initialized!"
    echo " Use './deploy.sh status' or './deploy.sh logs' to inspect."
    echo "=================================================================="
    ;;
  clone)
    cmd_clone
    ;;
  prepare)
    cmd_prepare
    ;;
  build)
    cmd_build
    ;;
  up)
    cmd_up
    ;;
  init)
    cmd_init
    ;;
  pull-data)
    cmd_pull_data
    ;;
  down)
    cmd_down
    ;;
  down-v)
    cmd_down_v
    ;;
  status)
    cmd_status
    ;;
  logs)
    cmd_logs "$@"
    ;;
  *)
    echo "Usage: $0 {all|clone|prepare|build|up|init|pull-data|down|down-v|status|logs [service]}"
    exit 1
    ;;
esac
