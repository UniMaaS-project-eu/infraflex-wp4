#!/usr/bin/env bash
# ==============================================================================
# Unified Deployment Script for Infraflex, Central-KG, ED-Node, and IDS-MVD-DMS
#
# Usage:
#   ./deploy.sh [command] [options] [services...]
#
# Commands:
#   all (default)  Clone external dataspace repos, prepare configs/keys, build images, start stack, init DBs & pull data
#   redeploy       Fast collaborator workflow: pull git changes, refresh configs, and run 'docker compose up -d [service...]'
#                    Options for 'redeploy':
#                      --build          Rebuild Docker image(s) before starting
#                      --pull-external  Also git pull external dataspace repos (central-kg, ed-node, ids-mvd-dms)
#                      --pull-data      Also re-run pull-data (reload AAS & Fuseki RDF)
#                      --no-git         Skip 'git pull' on the current repository
#   clone          Clone or update the 3 external dataspace repositories + submodules into ./repos
#   prepare        Generate .env, DID documents, Ed25519 keys, Vault & Nginx configs (connector1, connector2, connectorcp)
#   build          Build all (or specified) Docker images in the unified docker-compose.yml
#   up             Start all (or specified) services in detached mode
#   init           Initialize GraphDB repos, Postgres tracking table, and pull AAS/Fuseki data
#   pull-data      Pull & populate AAS shells/submodels and Fuseki RDF
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

# Auto-detect local Infraflex / Cloud-Based Resource Monitoring directory in the working tree
# (never re-clone this repository into ./repos)
if [[ -z "${INFRAFLEX_DIR:-}" ]] || [[ ! -d "${SCRIPT_DIR}/${INFRAFLEX_DIR}/basyx-setup" && ! -d "${INFRAFLEX_DIR}/basyx-setup" ]]; then
  if [[ -d "${SCRIPT_DIR}/../basyx-setup" ]]; then
    export INFRAFLEX_DIR=".."
  elif [[ -d "${SCRIPT_DIR}/../../cloud_based_resource_monitoring/basyx-setup" ]]; then
    export INFRAFLEX_DIR="../../cloud_based_resource_monitoring"
  else
    export INFRAFLEX_DIR="${INFRAFLEX_DIR:-..}"
  fi
fi

REPOS_PATH="$(mkdir -p "${REPOS_DIR:-./repos}" && cd "${REPOS_DIR:-./repos}" && pwd)"

init_submodules_resilient() {
  local repo_dir="$1"
  local skip_submodule="${2:-}"

  if [[ ! -f "${repo_dir}/.gitmodules" ]]; then
    return 0
  fi

  git -C "$repo_dir" submodule sync --recursive
  git -C "$repo_dir" submodule init

  local sub_keys
  sub_keys=$(git -C "$repo_dir" config --file .gitmodules --get-regexp '\.path$' | awk '{print $1}')

  for sk in $sub_keys; do
    local sp
    sp=$(git -C "$repo_dir" config --file .gitmodules --get "$sk")
    local url_key="${sk%.path}.url"
    local sub_url
    sub_url=$(git -C "$repo_dir" config --file .gitmodules --get "$url_key")
    local pinned_sha
    pinned_sha=$(git -C "$repo_dir" ls-tree HEAD "$sp" | awk '$2 == "commit" {print $3}')

    if [[ -n "$skip_submodule" && "$sp" == "$skip_submodule" ]]; then
      echo "[clone] Skipping redundant submodule '${sp}' (already cloned at top level)..."
      continue
    fi

    echo "[clone] Initializing / updating submodule '${sp}'..."
    if ! git -C "$repo_dir" submodule update --init --recursive --force "$sp"; then
      echo "[clone] Pinned commit not found on remote for '${sp}'; falling back to default branch from ${sub_url}..."
      if ! git -C "$repo_dir" submodule update --init --remote --force "$sp"; then
        rm -rf "${repo_dir}/${sp}"
        git clone --recurse-submodules "$sub_url" "${repo_dir}/${sp}"
      fi
    fi

    if [[ -z "$(ls -A "${repo_dir}/${sp}" 2>/dev/null | grep -v '^\.git$' || true)" ]]; then
      echo "[clone] Submodule '${sp}' working tree is empty; cloning default branch from ${sub_url}..."
      rm -rf "${repo_dir}/${sp}"
      git clone --recurse-submodules "$sub_url" "${repo_dir}/${sp}"
    fi

    # Ensure the submodule working tree is actually at the pinned commit (handles
    # directories previously created by the standalone git clone fallback).
    if [[ -n "$pinned_sha" && -d "${repo_dir}/${sp}" ]]; then
      local current_sha
      current_sha=$(git -C "${repo_dir}/${sp}" rev-parse HEAD 2>/dev/null || true)
      if [[ "$current_sha" != "$pinned_sha" ]]; then
        echo "[clone] Syncing '${sp}' from ${current_sha:-unknown} to pinned commit ${pinned_sha}..."
        git -C "${repo_dir}/${sp}" fetch origin || true
        if ! git -C "${repo_dir}/${sp}" checkout -f "$pinned_sha" 2>/dev/null; then
          echo "[clone] Pinned commit ${pinned_sha} unavailable for '${sp}'; updating to remote HEAD..."
          git -C "${repo_dir}/${sp}" checkout -f origin/HEAD 2>/dev/null || true
        fi
      fi
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
    if [[ "$name" == "ed-node" ]]; then
      # Preserve locally generated DID and Vault files (which match local keysPrivPub
      # and Vault state) across branch switches/updates such as master -> fm/integration,
      # where template/sample copies of those files are tracked in git.
      local backup_dir
      backup_dir="$(mktemp -d)"
      local preserved_files=()
      local inst
      for inst in connector1 connector2 "${ED_NODE_INSTANCE_NAME:-connectorcp}"; do
        if [[ -f "${target_dir}/deployment/assets/keysPrivPub/${inst}/${inst}_private.pem" ]]; then
          for rel in \
            "deployment/assets/dids/${inst}/.well-known/did.json" \
            "deployment/vault/${inst}/vault-${inst}-config.json"; do
            if [[ -f "${target_dir}/${rel}" && -s "${target_dir}/${rel}" ]]; then
              mkdir -p "${backup_dir}/$(dirname "$rel")"
              cp -p "${target_dir}/${rel}" "${backup_dir}/${rel}"
              preserved_files+=("$rel")
            fi
          done
        fi
      done
      local issuer_inst="${ISSUERSERVICEVC_INSTANCE_NAME:-issuerservicevc}"
      if [[ -f "${target_dir}/tests/issuerservice-vc/assets/keysPrivPub/${issuer_inst}/${issuer_inst}_private.pem" ]]; then
        for rel in \
          "tests/issuerservice-vc/assets/dids/${issuer_inst}/.well-known/did.json" \
          "tests/issuerservice-vc/vault/${issuer_inst}/vault-${issuer_inst}-config.json"; do
          if [[ -f "${target_dir}/${rel}" && -s "${target_dir}/${rel}" ]]; then
            mkdir -p "${backup_dir}/$(dirname "$rel")"
            cp -p "${target_dir}/${rel}" "${backup_dir}/${rel}"
            preserved_files+=("$rel")
          fi
        done
      fi

      chmod -R u+rwX "${target_dir}/deployment" "${target_dir}/tests/issuerservice-vc" 2>/dev/null || true

      for check_dir in \
        "${target_dir}/shared-storage" \
        "${target_dir}/deployment/assets/dids" \
        "${target_dir}/deployment/vault" \
        "${target_dir}/tests/issuerservice-vc/assets/dids" \
        "${target_dir}/tests/issuerservice-vc/vault"; do
        if [[ -e "$check_dir" && ! -w "$check_dir" ]]; then
          echo "Error: '${check_dir}' is not writable (likely created by Docker or root)."
          echo "       Run: sudo chown -R \"\$(id -u):\$(id -g)\" '${target_dir}' && chmod -R u+rwX '${target_dir}'"
          rm -rf "$backup_dir"
          exit 1
        fi
      done

      git -C "$target_dir" checkout -f -B "$branch" "origin/${branch}"

      local rel
      for rel in "${preserved_files[@]}"; do
        mkdir -p "${target_dir}/$(dirname "$rel")"
        chmod u+w "${target_dir}/${rel}" 2>/dev/null || true
        cp -f "${backup_dir}/${rel}" "${target_dir}/${rel}"
      done
      rm -rf "$backup_dir"
    else
      git -C "$target_dir" checkout "$branch"
      git -C "$target_dir" pull --ff-only origin "$branch" || true
    fi
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
  echo " Step 1: Cloning / updating external dataspace repos in ${REPOS_PATH}"
  echo "         Using local Infraflex working tree at: ${INFRAFLEX_DIR}"
  echo "=================================================================="
  clone_or_update_repo "central-kg" "${REPO_CENTRAL_KG_URL}" "${REPO_CENTRAL_KG_BRANCH}"
  clone_or_update_repo "ed-node" "${REPO_ED_NODE_URL}" "${REPO_ED_NODE_BRANCH}"
  clone_or_update_repo "ids-mvd-dms" "${REPO_DMS_URL}" "${REPO_DMS_BRANCH}" "central-kg"
  echo "[clone] All repositories ready in ${REPOS_PATH}."
}

remove_if_dir() {
  local path="$1"
  if [[ -d "$path" ]]; then
    echo "[prepare] Removing stale directory at file path '${path}'..."
    if ! rm -rf "$path" 2>/dev/null; then
      echo "Error: '${path}' is a directory (likely created by Docker as root) and could not be removed."
      echo "       Run: sudo rm -rf '${path}' && ./deploy.sh prepare"
      exit 1
    fi
  fi
}

verify_prepared_files() {
  local ed_node_dir="${REPOS_PATH}/ed-node"
  local issuer_dir="${ed_node_dir}/tests/issuerservice-vc"
  local issuer_instance="${ISSUERSERVICEVC_INSTANCE_NAME:-issuerservicevc}"
  local required_files=()

  for instance in connector1 connector2 "${ED_NODE_INSTANCE_NAME:-connectorcp}"; do
    required_files+=(
      "${ed_node_dir}/deployment/nginx/nginx-${instance}.conf"
      "${ed_node_dir}/deployment/vault/${instance}/vault-${instance}-config.json"
      "${ed_node_dir}/deployment/assets/keysPrivPub/${instance}/${instance}_private.pem"
      "${ed_node_dir}/deployment/assets/keysPrivPub/${instance}/${instance}_public.pem"
    )
  done

  required_files+=(
    "${issuer_dir}/nginx/nginx-${issuer_instance}.conf"
    "${issuer_dir}/assets/keysPrivPub/${issuer_instance}/${issuer_instance}_private.pem"
  )

  local missing=0
  local has_dirs=0
  for f in "${required_files[@]}"; do
    if [[ -d "$f" ]]; then
      echo "Error: Expected file but found directory: ${f}"
      has_dirs=1
      missing=1
    elif [[ ! -f "$f" || ! -s "$f" ]]; then
      echo "Error: Required generated file is missing or empty: ${f}"
      missing=1
    fi
  done

  if [[ "$missing" -ne 0 ]]; then
    echo ""
    if [[ "$has_dirs" -ne 0 ]]; then
      echo "Hint: Docker created empty directories where bind-mounted files were missing."
      echo "      Remove them first (using sudo if owned by root), then run './deploy.sh prepare':"
      echo "      sudo find '${ed_node_dir}' -type d \\( -name 'nginx-*.conf' -o -name 'vault-*-config.json' -o -name '*.pem' \\) -exec rm -rf {} +"
    fi
    echo "Run './deploy.sh prepare' before './deploy.sh up'."
    exit 1
  fi
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

  if ! command -v envsubst >/dev/null 2>&1; then
    echo "Error: 'envsubst' is not installed. Install it (e.g. 'sudo apt-get install gettext-base') and re-run './deploy.sh prepare'."
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
    local nginx_conf="${ed_node_dir}/deployment/nginx/nginx-${instance}.conf"
    local vault_conf="${ed_node_dir}/deployment/vault/${instance}/vault-${instance}-config.json"
    local priv_key="${ed_node_dir}/deployment/assets/keysPrivPub/${instance}/${instance}_private.pem"
    local pub_key="${ed_node_dir}/deployment/assets/keysPrivPub/${instance}/${instance}_public.pem"

    remove_if_dir "$nginx_conf"
    remove_if_dir "$vault_conf"
    remove_if_dir "$priv_key"
    remove_if_dir "$pub_key"

    if [[ ! -f "$priv_key" || ! -f "$pub_key" || ! -f "$vault_conf" ]]; then
      echo "[prepare] Generating Ed25519 keys, DID document, and Vault config for '${instance}'..."
      (
        cd "$ed_node_dir"
        chmod +x generateEDNodeFiles-KeysDIDVault.sh
        ./generateEDNodeFiles-KeysDIDVault.sh "$instance" "http://${did_host_port}"
      )
    else
      echo "[prepare] Keys, DID, and Vault config for '${instance}' already exist, skipping key generation."
    fi

    echo "[prepare] Rendering Nginx config for '${instance}'..."
    ED_NODE_INSTANCE_NAME="$instance" envsubst '$ED_NODE_INSTANCE_NAME' \
      < "${ed_node_dir}/deployment/nginx/nginx-template.conf" \
      > "$nginx_conf"
  done

  # 2. Generate IssuerService-VC keys, DID, and Nginx config
  local issuer_instance="${ISSUERSERVICEVC_INSTANCE_NAME:-issuerservicevc}"
  local issuer_host_port="${UNIMAAS_ISSUERSERVICEVC_HOST:-edc-proxy-issuerservicevc}:${UNIMAAS_ISSUERSERVICEVC_DID_PORT:-80}"
  local issuer_nginx_conf="${issuer_dir}/nginx/nginx-${issuer_instance}.conf"
  local issuer_priv_key="${issuer_dir}/assets/keysPrivPub/${issuer_instance}/${issuer_instance}_private.pem"
  local issuer_pub_key="${issuer_dir}/assets/keysPrivPub/${issuer_instance}/${issuer_instance}_public.pem"

  remove_if_dir "$issuer_nginx_conf"
  remove_if_dir "$issuer_priv_key"
  remove_if_dir "$issuer_pub_key"

  if [[ ! -f "$issuer_priv_key" || ! -f "$issuer_pub_key" ]]; then
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
    > "$issuer_nginx_conf"

  # Ensure shared-storage directory exists for RDF file assets (/shared-storage)
  mkdir -p "${ed_node_dir}/shared-storage"

  chmod +x "${SCRIPT_DIR}/scripts/"*.sh "${SCRIPT_DIR}/scripts/"*.py 2>/dev/null || true

  echo "[prepare] Preparation complete."
}

cmd_build() {
  echo "=================================================================="
  echo " Step 3: Building Docker images"
  echo "=================================================================="
  docker compose --env-file "$ENV_FILE" -f "${SCRIPT_DIR}/docker-compose.yml" build "$@"
}

cmd_up() {
  echo "=================================================================="
  echo " Step 4: Starting unified Docker Compose stack"
  echo "=================================================================="
  verify_prepared_files
  docker compose --env-file "$ENV_FILE" -f "${SCRIPT_DIR}/docker-compose.yml" up -d "$@"
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

cmd_redeploy() {
  local do_build=false
  local do_pull_ext=false
  local do_pull_data=false
  local do_git=true
  local services=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --build)
        do_build=true
        shift
        ;;
      --pull-external)
        do_pull_ext=true
        shift
        ;;
      --pull-data)
        do_pull_data=true
        shift
        ;;
      --no-git)
        do_git=false
        shift
        ;;
      *)
        services+=("$1")
        shift
        ;;
    esac
  done

  echo "=================================================================="
  echo " Fast Collaborator Redeploy"
  echo "=================================================================="

  # 1. Pull latest changes on the current git repository
  if [[ "$do_git" == "true" ]]; then
    local git_root
    git_root="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$git_root" ]]; then
      echo "[redeploy] Pulling latest changes in repository (${git_root})..."
      git -C "$git_root" pull --ff-only || echo "[redeploy] Note: 'git pull --ff-only' skipped or had local changes; continuing with current working tree."
    fi
  fi

  # 2. If external dataspace repos are missing (first run) or --pull-external was requested, sync them
  if [[ ! -d "${REPOS_PATH}/ed-node" || ! -d "${REPOS_PATH}/central-kg" || ! -d "${REPOS_PATH}/ids-mvd-dms" || "$do_pull_ext" == "true" ]]; then
    cmd_clone
  fi

  # 3. Re-apply lightweight config templates (idempotent; skips key generation if keys exist)
  cmd_prepare

  # 4. Optionally rebuild images if --build was passed
  if [[ "$do_build" == "true" ]]; then
    cmd_build "${services[@]}"
  fi

  # 5. Recreate only changed (or targeted) containers
  cmd_up "${services[@]}"

  # 6. Optionally reload AAS & Fuseki data if --pull-data was passed
  if [[ "$do_pull_data" == "true" ]]; then
    cmd_pull_data
  fi

  echo "=================================================================="
  echo " Redeploy complete!"
  echo "=================================================================="
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
  docker compose --env-file "$ENV_FILE" -f "${SCRIPT_DIR}/docker-compose.yml" logs -f "$@"
}

ACTION="${1:-all}"
shift || true

case "$ACTION" in
  all)
    cmd_clone
    cmd_prepare
    cmd_build
    cmd_up
    cmd_init
    echo "=================================================================="
    echo " Unified deployment is up and initialized!"
    echo " Use './deploy.sh redeploy' after pulling future changes,"
    echo " or './deploy.sh status' / './deploy.sh logs' to inspect."
    echo "=================================================================="
    ;;
  redeploy|update)
    cmd_redeploy "$@"
    ;;
  clone)
    cmd_clone
    ;;
  prepare)
    cmd_prepare
    ;;
  build)
    cmd_build "$@"
    ;;
  up)
    cmd_up "$@"
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
    echo "Usage: $0 {all|redeploy [--build] [--pull-external] [--pull-data] [--no-git] [service...]|clone|prepare|build [service...]|up [service...]|init|pull-data|down|down-v|status|logs [service...]}"
    exit 1
    ;;
esac
