# UniMaaS Shared Repository — Dataspace & Cloud-Based Resource Monitoring

This repository organizes the shared UniMaaS software artifacts and deployment bundles into two primary modules:

1. **[`dataspace/`](./dataspace/)** — Dataspace Management Suite (DMS), Central Knowledge Graph, and ED-Node connectors, including links to the constituent repositories (`central-kg`, `ed-node`, `ids-mvd-dms`) and the unified multi-repository Docker Compose deployment bundle (`dataspace/deploy/`).
2. **[`cloud_based_resource_monitoring/`](./cloud_based_resource_monitoring/)** — Cloud-based shopfloor resource monitoring pipeline connecting MQTT telemetry, Virtual Objects (WoT), Kafka/Redpanda event streaming, Eclipse BaSyx V2 Asset Administration Shells (`AAS/`), and Ontotext GraphDB Knowledge Graph synchronization (`unimaas_infraflex/`).
