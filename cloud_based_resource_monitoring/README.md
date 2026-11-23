# UniMaaS Cloud-Based Resource Monitoring

Real-time shopfloor resource monitoring pipeline connecting IIoT/MQTT device telemetry to W3C Web of Things (WoT) Virtual Objects, Kafka/Redpanda event streaming, Eclipse BaSyx V2 Asset Administration Shells (`AAS/`), and an Ontotext GraphDB Knowledge Graph aligned with the UniMaaS Manufacturing Service Chain (`MSC`) Ontology.

---

## Architecture Overview

```
Shopfloor MQTT Telemetry (Mosquitto :1883)
    │
    ▼
unimaas_infraflex/vo_layer/vo_manager.py     Normalizes MQTT payloads via WoT Thing Descriptions
    │                                        and publishes updates to Kafka/Redpanda (:19092)
    ▼
unimaas_infraflex/kafka/aas_consumer.py      Consumes Kafka events and PATCHes LiveData submodels
    │                                        in Eclipse BaSyx AAS Repository (:8081)
    ▼
Eclipse BaSyx V2 AAS Environment (:8081)     Stores AAS shells & submodels (UI on :3000)
    │  polled every 3 s
    ▼
unimaas_infraflex/kg/infraflex2kg.py         Synchronizes AAS shells & operational state into
    │                                        Ontotext GraphDB (:7200)
    ▼
Ontotext GraphDB Knowledge Graph (:7200)
```

---

## Quick Start

### 1. Populate BaSyx Startup AAS Directory

Copy the AAS V3 JSON definitions from `AAS/` into `basyx-setup/aas/` so BaSyx loads them on startup:

```bash
cp AAS/*/*.json basyx-setup/aas/
```

### 2. Start the Stack

```bash
docker compose up -d --build
```

### 3. Active Services & Endpoints

| Container | Endpoint | Role |
| :--- | :--- | :--- |
| `aas-ui` | `http://localhost:3000` | Eclipse BaSyx AAS Web UI |
| `aas-env` | `http://localhost:8081` | AAS, Submodel, and ConceptDescription Repository |
| `aas-registry` | `http://localhost:8082` | AAS Registry (`/shell-descriptors`) |
| `sm-registry` | `http://localhost:8083` | Submodel Registry (`/submodel-descriptors`) |
| `aas-discovery` | `http://localhost:8084` | AAS Discovery (`/lookup/shells`) |
| `dashboard-api` | `http://localhost:8085` | BaSyx UI Dashboard Service |
| `graphdb` | `http://localhost:7200` | Ontotext GraphDB Workbench & SPARQL Endpoint |
| `mosquitto` | `mqtt://localhost:1883` | MQTT Broker for shopfloor telemetry |
| `redpanda` | `localhost:19092` | Kafka-compatible event streaming broker |
| `unimaas-infraflex` | *(background daemon)* | Runs `vo_manager.py`, `aas_consumer.py`, and `infraflex2kg.py` |

---

## Directory Structure

```
cloud_based_resource_monitoring/
├── docker-compose.yml            Docker Compose stack (BaSyx, GraphDB, Redpanda, Mosquitto, unimaas-infraflex)
├── Dockerfile                    Container image definition for unimaas_infraflex
├── requirements.txt              Python dependencies
├── basyx-setup/                  BaSyx & Mosquitto configuration files
│   ├── aas/                      Startup AAS directory (populated from AAS/)
│   ├── basyx/                    BaSyx properties & registry YAML files
│   └── mosquitto/                Mosquitto broker configuration
├── AAS/                          MSC Ontology-aligned AAS V3 JSON models
│   ├── addons/                   Add-on AAS definitions
│   ├── arms/                     Robot arm AAS definitions
│   ├── cells/                    Manufacturing cell AAS definitions
│   ├── characteristics/          Capability & property characteristics
│   ├── executors/                Shopfloor executor AAS definitions (LiveData submodels)
│   ├── processes/                Manufacturing process & step AAS definitions
│   ├── products/                 Product & component AAS definitions
│   ├── routes/                   Shopfloor transit route AAS definitions
│   ├── sensors/                  Sensor AAS definitions
│   └── tools/                    End-effector tool AAS definitions
├── ontologies/                   UniMaaS MSC Ontology & Infraflex instance ontology
│   ├── MSC-ontology-1.2.0.ttl    UniMaaS Manufacturing Service Chain Ontology
│   ├── next-steps.ttl            MSC stage & step transition rules
│   └── instances/
│       └── infraflex_instances.ttl  MSC-based Infraflex ontology generated from AAS/
└── unimaas_infraflex/            Core Python monitoring & synchronization package
    ├── config/devices.json       Device, topic, BaSyx, and GraphDB mappings
    ├── vo_layer/                 MQTT → WoT Virtual Object → Kafka bridge
    ├── kafka/                    Kafka → BaSyx LiveData submodel consumer
    ├── kg/                       BaSyx → Ontotext GraphDB synchronizer
    └── ontology_bridge/          AAS-to-MSC Ontology export and generator utilities
```
