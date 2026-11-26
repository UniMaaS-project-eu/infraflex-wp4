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

## SPARQL Queries for the Local Infraflex Knowledge Graph (`:7200` / `infraflex`)

Open the Ontotext GraphDB Workbench at `http://localhost:7200` (or `http://<EXTERNAL_HOST>:7201` when deployed via `dataspace/deploy`), select the **`infraflex`** repository, and run:

```sparql
PREFIX sc: <http://unimaas-project.eu/MSCOntology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

# 1. Count all MSC Ontology entity types in the local Infraflex KG
SELECT ?mscClass (COUNT(DISTINCT ?entity) AS ?count) WHERE {
    ?entity a ?mscClass .
    FILTER(STRSTARTS(STR(?mscClass), "http://unimaas-project.eu/MSCOntology#"))
}
GROUP BY ?mscClass
ORDER BY DESC(?count)

# 2. Manufacturing Cells (sc:Site) and their mounted Devices / Robot Arms
SELECT ?cellName ?deviceName ?deviceType WHERE {
    ?cell a sc:Site ;
          rdfs:label ?cellName ;
          sc:hasDevice ?dev .
    ?dev a ?deviceType ;
         rdfs:label ?deviceName .
    FILTER(?deviceType IN (sc:Device, sc:EquipmentResource))
}
ORDER BY ?cellName ?deviceName

# 3. Live operational state, active skill, and last sync timestamp of all shopfloor devices
SELECT ?device ?state ?skill ?taskState ?errorCode ?lastSync WHERE {
    ?d a sc:Device ;
       rdfs:label ?device .
    OPTIONAL { ?d sc:operationalState ?state }
    OPTIONAL { ?d sc:currentSkill ?skill }
    OPTIONAL { ?d sc:taskState ?taskState }
    OPTIONAL { ?d sc:errorCode ?errorCode }
    OPTIONAL { ?d sc:lastUpdated ?lastSync }
}
ORDER BY DESC(?lastSync) ?device

# 4. Sensors and the Characteristics they observe (with current values and units)
SELECT ?sensorName ?sensorType ?charName ?value ?unit WHERE {
    ?sensor a sc:Sensor ;
            rdfs:label ?sensorName ;
            sc:observes ?char .
    OPTIONAL { ?sensor sc:sensorType ?sensorType }
    ?char rdfs:label ?charName .
    OPTIONAL { ?char sc:characteristicValue ?value }
    OPTIONAL { ?char sc:unit ?unit }
}
ORDER BY ?sensorName ?charName

# 5. Process configurations per cell and the equipment resources they use
SELECT ?configName ?processName ?siteName ?resourceName WHERE {
    ?cfg a sc:ProcessConfiguration ;
         rdfs:label ?configName .
    OPTIONAL { ?cfg sc:requiresProcess/rdfs:label ?processName }
    OPTIONAL { ?cfg sc:isPerformedAt/rdfs:label ?siteName }
    OPTIONAL { ?cfg sc:usesResource/rdfs:label ?resourceName }
}
ORDER BY ?configName ?resourceName
```

Or query directly from the terminal via `curl`:

```bash
curl -s -G "http://localhost:7200/repositories/infraflex"   -H "Accept: application/sparql-results+json"   --data-urlencode "query=PREFIX sc: <http://unimaas-project.eu/MSCOntology#> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT ?device ?state ?skill ?lastSync WHERE { ?d a sc:Device ; rdfs:label ?device . OPTIONAL { ?d sc:operationalState ?state } OPTIONAL { ?d sc:currentSkill ?skill } OPTIONAL { ?d sc:lastUpdated ?lastSync } } ORDER BY ?device"
```

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
