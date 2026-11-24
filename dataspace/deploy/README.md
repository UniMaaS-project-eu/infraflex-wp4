# Unified Deployment Bundle

This folder contains a self-contained deployment bundle to clone and deploy all four repositories onto an external host using a single `docker-compose.yml`:

1. **`unimaas-infraflex`** (`https://github.com/Flanders-Make-vzw/unimaas-infraflex.git`, branch `main`) — Eclipse BaSyx AAS stack, Mosquitto, Redpanda, Infraflex GraphDB, `unimaas-infraflex` consumer/bridge, and `infraplay`.
2. **`central-kg`** (`https://github.com/hmarah-fm/central-kg.git`, branch `main`) — Central Platform GraphDB (`centralKG`), PostgreSQL (`postgrescp`), and Central KG OpenAPI (`centralkg-api`).
3. **`ed-node`** (`https://github.com/hmarah-fm/ed-node.git`, branch `master`) — Three containerized EDC Node participants (`connector1`, `connector2`, and `connectorcp`), HashiCorp Vault (with auto-init and auto-unseal), Identity Hubs, Mock Registry, and VC Issuer Service (`issuerservicevc`).
4. **`ids-mvd-dms`** (`https://github.com/hmarah-fm/ids-mvd-dms.git`, branch `main`) — Keycloak realm configuration (`unimaas`), Provider Jena Fuseki triple store, Standalone Federated Catalog, and Digital Thread ingestion agent.

---

## Quick Start on an External Host

1. Copy the `deploy/` directory to the target host.
2. (Optional) Copy `.env.example` to `.env` and set `EXTERNAL_HOST=<your-host-ip-or-domain>` (if left as `localhost`, `./deploy.sh prepare` auto-detects the host IP):
   ```bash
   cp .env.example .env
   ```
3. Run the unified deployment script:
   ```bash
   chmod +x deploy.sh
   ./deploy.sh
   ```

`./deploy.sh` executes five stages automatically (or individually via `./deploy.sh <command>`):
- `./deploy.sh clone` — Clones (or updates) all 4 repositories and initializes their recursive Git submodules inside `./repos/`.
- `./deploy.sh prepare` — Auto-detects `EXTERNAL_HOST` and generates Ed25519 keypairs, `did:web` documents, Vault configs, and Nginx proxy configs for `connector1`, `connector2`, `connectorcp`, and `issuerservicevc`.
- `./deploy.sh build` — Builds all Docker images (including multi-stage Gradle builds for `standalone-federated-catalog` and `digital-thread` via `dockerfiles/`).
- `./deploy.sh up` — Starts all services on the shared `edc-network` via `docker compose up -d`.
- `./deploy.sh init` — Initializes Postgres (`inserted_assets`) and GraphDB (`centralKG`), and runs `./deploy.sh pull-data`.
- `./deploy.sh pull-data` — Prepares and uploads all AAS shells/submodels into BaSyx (`:8081`, `:8082`, `:8083`) and preloads the provider RDF dataset into Jena Fuseki (`:3032`).
- `./deploy.sh status` — Displays the status and health of all containers.
- `./deploy.sh logs [service]` — Tails logs of all services or a specific service.
- `./deploy.sh down` / `./deploy.sh down-v` — Stops and removes containers (and volumes with `down-v`).

---

## Active Endpoints Reference

Replace `<EXTERNAL_HOST>` with `localhost` or the IP/hostname configured in `.env`.

### 1. Infraflex & Eclipse BaSyx Stack

| Service | Container Name | External Endpoint / URL | Internal Docker Address | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **BaSyx AAS Web UI** | `aas-ui` | `http://<EXTERNAL_HOST>:3000` | `http://aas-web-ui:3000` | Web dashboard for browsing AAS shells and submodels |
| **BaSyx AAS Environment** | `aas-env` | `http://<EXTERNAL_HOST>:8081/shells`<br>`http://<EXTERNAL_HOST>:8081/submodels`<br>`http://<EXTERNAL_HOST>:8081/concept-descriptions` | `http://aas-env:8081` | AAS, Submodel, and ConceptDescription repositories |
| **BaSyx AAS Registry** | `aas-registry` | `http://<EXTERNAL_HOST>:8082/shell-descriptors` | `http://aas-registry:8080` | Shell descriptor registry |
| **BaSyx Submodel Registry** | `sm-registry` | `http://<EXTERNAL_HOST>:8083/submodel-descriptors` | `http://sm-registry:8080` | Submodel descriptor registry |
| **BaSyx AAS Discovery** | `aas-discovery` | `http://<EXTERNAL_HOST>:8084/lookup/shells` | `http://aas-discovery:8081` | Asset-to-AAS discovery lookup |
| **BaSyx Dashboard API** | `dashboard-api` | `http://<EXTERNAL_HOST>:8085/api/elements` | `http://dashboard-api:8085` | Backing API for BaSyx UI dashboard elements |
| **Infraplay Web UI** | `infraplay` | `http://<EXTERNAL_HOST>:8090` | `http://infraplay:8090` | Infraflex interactive scenario replay & harvesting studio |
| **Infraflex GraphDB** | `infraflex-graphdb` | `http://<EXTERNAL_HOST>:7201`<br>`http://<EXTERNAL_HOST>:7201/repositories/infraflex` | `http://infraflex-graphdb:7200` | Dedicated GraphDB instance for `infraflex` repository |
| **Mosquitto MQTT Broker** | `mosquitto` | `mqtt://<EXTERNAL_HOST>:1883` | `mosquitto:1883` | Shopfloor & executor telemetry MQTT broker |
| **Redpanda Kafka API** | `redpanda` | `<EXTERNAL_HOST>:19092` | `redpanda:9092` | Kafka-compatible event streaming API |
| **Redpanda Pandaproxy** | `redpanda` | `http://<EXTERNAL_HOST>:18083` | `http://redpanda:8083` | REST proxy for Redpanda topics |
| **Infraflex Bridge** | `unimaas-infraflex` | *(background worker)* | `unimaas-infraflex` | Consumes MQTT/Kafka & synchronizes BaSyx and `infraflex-graphdb` |

---

### 2. Central-KG, ED-Node & Dataspace Management Suite (DMS) Stack

| Service | Container Name | External Endpoint / URL | Internal Docker Address | Notes / Default Credentials |
| :--- | :--- | :--- | :--- | :--- |
| **Central Platform GraphDB** | `central-platform-graphdb` | `http://<EXTERNAL_HOST>:7200`<br>`http://<EXTERNAL_HOST>:7200/repositories/centralKG` | `http://central-platform-graphdb:7200` | Central MSC Knowledge Graph (`admin` / `mypassword`) |
| **Central KG FastAPI** | `central-platform-kg-api` | `http://<EXTERNAL_HOST>:8000`<br>`http://<EXTERNAL_HOST>:8000/docs` | `http://central-platform-kg-api:8000` | OpenAPI / Swagger UI for MSC & SPARQL queries (`/classes`, `/instances`, `/sparql`, `/msc/get_stage`) |
| **Central Platform Postgres** | `central-platform-postgres` | `postgresql://<EXTERNAL_HOST>:5432/postgrescp` | `central-platform-postgres:5432` | Tracks ingested assets in `inserted_assets` (`admin` / `admin`) |
| **Federated Catalog API** | `central-platform-federated-catalog` | `http://<EXTERNAL_HOST>:8184/api/catalog/v1alpha/catalog/query` | `http://central-platform-federated-catalog:8184/api/catalog` | Standalone federated catalog query endpoint (`x-api-key: password`) |
| **Federated Catalog Mgmt** | `central-platform-federated-catalog` | `http://<EXTERNAL_HOST>:8181/api/management` | `http://central-platform-federated-catalog:8181/api/management` | Federated catalog management API (`x-api-key: password`) |
| **Digital Thread Agent** | `central-platform-digital-thread` | *(background worker)* | `central-platform-digital-thread` | Polls federated catalog, negotiates EDC transfers, and ingests RDF into `centralKG` |
| **Provider Jena Fuseki** | `fuseki-server-provider` | `http://<EXTERNAL_HOST>:3032`<br>`http://<EXTERNAL_HOST>:3032/provider/query` | `http://fuseki-server-provider:3030` | Provider SPARQL endpoint & RDF triple store (`admin` / `mypassword`) |
| **Keycloak OIDC & Directory** | `keycloak` | `http://<EXTERNAL_HOST>:5080`<br>`http://<EXTERNAL_HOST>:5080/realms/unimaas/.well-known/openid-configuration` | `http://keycloak:8080` | Participant directory & OIDC IDP (`admin` / `admin`, realm `unimaas`) |
| **Connector 1 Gateway & APIs** | `edc-proxy-connector1`<br>`edc-connector-connector1`<br>`edc-ih-connector1` | **Proxy**: `http://<EXTERNAL_HOST>:9876`<br>**DID**: `http://<EXTERNAL_HOST>:9876/connector1/.well-known/did.json`<br>**Mgmt**: `http://<EXTERNAL_HOST>:8191/api/management/v3/`<br>**DSP**: `http://<EXTERNAL_HOST>:8192/api/dsp`<br>**Public**: `http://<EXTERNAL_HOST>:11002/api/public`<br>**IH Ports**: `7190`–`7195` | `http://edc-proxy-connector1:80`<br>`http://edc-connector-connector1:8080`<br>`http://edc-ih-connector1:7090` | Provider 1 ED-Node (`connector1`) |
| **Connector 2 Gateway & APIs** | `edc-proxy-connector2`<br>`edc-connector-connector2`<br>`edc-ih-connector2` | **Proxy**: `http://<EXTERNAL_HOST>:9877`<br>**DID**: `http://<EXTERNAL_HOST>:9877/connector2/.well-known/did.json`<br>**Mgmt**: `http://<EXTERNAL_HOST>:8291/api/management/v3/`<br>**DSP**: `http://<EXTERNAL_HOST>:8292/api/dsp`<br>**Public**: `http://<EXTERNAL_HOST>:12002/api/public`<br>**IH Ports**: `7290`–`7295` | `http://edc-proxy-connector2:80`<br>`http://edc-connector-connector2:8080`<br>`http://edc-ih-connector2:7090` | Provider 2 ED-Node (`connector2`) |
| **Connector CP Gateway & APIs** | `edc-proxy-connectorcp`<br>`edc-connector-connectorcp`<br>`edc-ih-connectorcp` | **Proxy**: `http://<EXTERNAL_HOST>:9878`<br>**DID**: `http://<EXTERNAL_HOST>:9878/connectorcp/.well-known/did.json`<br>**Mgmt**: `http://<EXTERNAL_HOST>:8391/api/management/v3/`<br>**DSP**: `http://<EXTERNAL_HOST>:8392/api/dsp`<br>**Public**: `http://<EXTERNAL_HOST>:11001/api/public`<br>**IH Ports**: `7090`–`7095` | `http://edc-proxy-connectorcp:80`<br>`http://edc-connector-connectorcp:8080`<br>`http://edc-ih-connectorcp:7090` | Central Platform Consumer ED-Node (`connectorcp`) |
| **IssuerService-VC Proxy** | `edc-proxy-issuerservicevc` | `http://<EXTERNAL_HOST>:7000/health`<br>`http://<EXTERNAL_HOST>:7000/issuerservicevc/.well-known/did.json`<br>`http://<EXTERNAL_HOST>:7000/api/v1/health`<br>`http://<EXTERNAL_HOST>:7000/api/v1/issue-credential` | `http://edc-proxy-issuerservicevc:80` | Verifiable Credential issuer & DID resolver (`issuerservicevc`) |
| **Mock Context Registry** | `mock-registry` | `http://<EXTERNAL_HOST>:8089/health`<br>`http://<EXTERNAL_HOST>:8089/main/context/2411` | `http://mock-registry:8080` | Local JSON-LD `@context` resolver for GAIA-X / VC credentials |

---

## Quick Verification Commands

```bash
# 1. Pull/reload AAS shells, InfraPlay scenarios, TimescaleDB logs, and Fuseki data at any time
./deploy.sh pull-data

# 2. Verify BaSyx AAS Shells & Infraflex GraphDB
curl -s http://localhost:8081/shells | head -c 300
curl -s http://localhost:7201/rest/repositories

# 3. Verify Jena Fuseki Provider Dataset (:3032)
curl -s -u admin:mypassword "http://localhost:3032/provider/query?query=SELECT%20(COUNT(*)%20AS%20%3Fcount)%20WHERE%20%7B%20%3Fs%20%3Fp%20%3Fo%20%7D"

# 4. Verify All 3 ED-Node Connectors (connector1, connector2, connectorcp)
curl -s http://localhost:9876/connector1/.well-known/did.json
curl -s http://localhost:9877/connector2/.well-known/did.json
curl -s http://localhost:9878/connectorcp/.well-known/did.json
curl -s http://localhost:8190/api/check/health
curl -s http://localhost:8290/api/check/health
curl -s http://localhost:8390/api/check/health
```
