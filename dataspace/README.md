# UniMaaS Dataspace & Data Management Suite (DMS)

This directory contains the unified integration and deployment bundle for the UniMaaS Dataspace and points to the upstream component repositories maintained by the consortium.

---

## Constituent Repositories

| Component | Repository URL | Default Branch | Role in the UniMaaS Dataspace |
| :--- | :--- | :--- | :--- |
| **Central Knowledge Graph (`central-kg`)** | [`https://github.com/hmarah-fm/central-kg.git`](https://github.com/hmarah-fm/central-kg.git) | `main` | Central Platform Ontotext GraphDB (`centralKG`), PostgreSQL tracking database (`postgrescp`), MSC Ontology configuration (`MSC-Ontology-RDF`), and Central KG FastAPI service (`centralKG_API`). |
| **EDC Node (`ed-node`)** | [`https://github.com/hmarah-fm/ed-node.git`](https://github.com/hmarah-fm/ed-node.git) | `master` | Containerized Eclipse Dataspace Connector (EDC) node template (`connector1`, `connector2`, `connectorcp`), HashiCorp Vault with automated initialization & unsealing, Identity Hub, Mock Context Registry, and Verifiable Credential Issuer Service (`issuerservicevc`). |
| **IDS MVD Data Management Suite (`ids-mvd-dms`)** | [`https://github.com/hmarah-fm/ids-mvd-dms.git`](https://github.com/hmarah-fm/ids-mvd-dms.git) | `main` | Keycloak OIDC participant directory (`unimaas` realm), Provider Jena Fuseki RDF triple store, Standalone Federated Catalog, and Digital Thread ingestion orchestrator. |
| **MSC Ontology RDF (`MSC-Ontology-RDF`)** | [`https://github.com/UniMaaS-project-eu/MSC-Ontology-RDF.git`](https://github.com/UniMaaS-project-eu/MSC-Ontology-RDF.git) | `main` | Manufacturing Service Chain (MSC) semantic ontology definitions and reasoning rules. |

---

## Unified Deployment (`./deploy`)

The [`deploy/`](./deploy/) subfolder provides a turnkey, single-host deployment script (`deploy.sh`) and unified `docker-compose.yml` that automatically clones the repositories above, generates cryptographic keys and `did:web` documents, builds all container images, and starts the complete dataspace and resource monitoring stack.

See [`deploy/README.md`](./deploy/README.md) for step-by-step instructions and the complete endpoint reference.
