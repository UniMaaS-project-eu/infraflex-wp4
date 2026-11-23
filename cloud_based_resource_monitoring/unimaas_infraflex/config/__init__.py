"""Configuration loader with environment variable overrides for Docker deployments."""

import json
import os
from pathlib import Path
from typing import Any


def load_config(config_path: str | Path) -> dict[str, Any]:
    """Load devices.json and apply optional environment variable overrides."""
    with open(config_path) as f:
        cfg: dict[str, Any] = json.load(f)

    # MQTT overrides
    mqtt_cfg = cfg.setdefault("mqtt", {})
    if os.environ.get("MQTT_HOST"):
        mqtt_cfg["host"] = os.environ["MQTT_HOST"]
    if os.environ.get("MQTT_PORT"):
        mqtt_cfg["port"] = int(os.environ["MQTT_PORT"])
    if os.environ.get("MQTT_USERNAME"):
        mqtt_cfg["username"] = os.environ["MQTT_USERNAME"]
    if os.environ.get("MQTT_PASSWORD"):
        mqtt_cfg["password"] = os.environ["MQTT_PASSWORD"]
    if os.environ.get("MQTT_BASE_TOPIC"):
        mqtt_cfg["base_topic"] = os.environ["MQTT_BASE_TOPIC"]

    # Kafka overrides
    kafka_cfg = cfg.setdefault("kafka", {})
    if os.environ.get("KAFKA_BOOTSTRAP_SERVERS"):
        kafka_cfg["bootstrap_servers"] = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
    if os.environ.get("KAFKA_TOPIC"):
        kafka_cfg["topic"] = os.environ["KAFKA_TOPIC"]

    # BaSyx overrides
    basyx_cfg = cfg.setdefault("basyx", {})
    if os.environ.get("BASYX_AAS_REPO"):
        basyx_cfg["aas_repo"] = os.environ["BASYX_AAS_REPO"]
        basyx_cfg["submodel_repo"] = os.environ.get(
            "BASYX_SUBMODEL_REPO", os.environ["BASYX_AAS_REPO"]
        )
    if os.environ.get("BASYX_SUBMODEL_REPO"):
        basyx_cfg["submodel_repo"] = os.environ["BASYX_SUBMODEL_REPO"]
    if os.environ.get("BASYX_AAS_REGISTRY"):
        basyx_cfg["aas_registry"] = os.environ["BASYX_AAS_REGISTRY"]

    # GraphDB overrides
    graphdb_cfg = cfg.setdefault("graphdb", {})
    if os.environ.get("GRAPHDB_URL"):
        graphdb_cfg["url"] = os.environ["GRAPHDB_URL"]
    if os.environ.get("GRAPHDB_REPOSITORY"):
        graphdb_cfg["repository"] = os.environ["GRAPHDB_REPOSITORY"]
    if os.environ.get("GRAPHDB_USER"):
        graphdb_cfg["user"] = os.environ["GRAPHDB_USER"]
    if os.environ.get("GRAPHDB_PASSWORD"):
        graphdb_cfg["password"] = os.environ["GRAPHDB_PASSWORD"]

    # Legacy Neo4j overrides fallback (if configured)
    if "neo4j" in cfg:
        neo4j_cfg = cfg["neo4j"]
        if os.environ.get("NEO4J_URI"):
            neo4j_cfg["uri"] = os.environ["NEO4J_URI"]
        if os.environ.get("NEO4J_USER"):
            neo4j_cfg["user"] = os.environ["NEO4J_USER"]
        if os.environ.get("NEO4J_PASSWORD"):
            neo4j_cfg["password"] = os.environ["NEO4J_PASSWORD"]
        if os.environ.get("NEO4J_DATABASE"):
            neo4j_cfg["database"] = os.environ["NEO4J_DATABASE"]

    return cfg

