#!/usr/bin/env python3
"""
AAS Consumer — Kafka-to-BaSyx bridge.

Reads property-update events produced by vo_manager.py from the Kafka topic
and applies them to the corresponding AAS submodel elements in BaSyx via REST API.

Flow per event:
  Kafka infraflex.vo.property.updates
    -> identify AAS shell + submodel + element
    -> PATCH /submodels/{submodelId}/submodel-elements/{idShort}/$value
    -> BaSyx AAS Repository

Run:
  python aas_consumer.py [--config path/to/devices.json]
"""

import base64
import json
import logging
import sys
import time
from pathlib import Path

import requests

try:
    from kafka import KafkaConsumer
    KAFKA_AVAILABLE = True
except Exception:
    KAFKA_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("aas_consumer")


# ---------------------------------------------------------------------------
# BaSyx REST helpers
# ---------------------------------------------------------------------------

def _b64url(text: str) -> str:
    """Base64-URL-encode a string (no padding) — required by BaSyx 2.x API."""
    return base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode()


def _xs_type(value) -> str:
    """Map a Python value to an xs: type string for BaSyx."""
    if isinstance(value, bool):
        return "xs:boolean"
    if isinstance(value, int):
        return "xs:int"
    if isinstance(value, float):
        return "xs:double"
    return "xs:string"


class BaSyxUpdater:
    """Applies VO property updates to BaSyx via its REST API."""

    def __init__(self, aas_repo_url: str, session: requests.Session | None = None):
        self.repo = aas_repo_url.rstrip("/")
        self._session = session or requests.Session()

    def update_submodel_element(
        self,
        submodel_id: str,
        element_id_short: str,
        value,
    ) -> bool:
        """
        PATCH the value of a submodel element.
        Uses:  PATCH /submodels/{base64url(submodelId)}/submodel-elements/{idShort}/$value
        """
        sm_encoded = _b64url(submodel_id)
        url = f"{self.repo}/submodels/{sm_encoded}/submodel-elements/{element_id_short}/$value"

        # BaSyx 2.x expects the value wrapped as a ValueOnly JSON body
        if isinstance(value, bool):
            formatted = "true" if value else "false"
        else:
            formatted = str(value)
        body = json.dumps(formatted)

        try:
            resp = self._session.patch(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                timeout=5,
            )
            if resp.status_code in (200, 204):
                log.debug("Updated %s / %s = %r", submodel_id, element_id_short, value)
                return True
            else:
                log.warning(
                    "BaSyx returned %s for %s / %s: %s",
                    resp.status_code, submodel_id, element_id_short, resp.text[:200]
                )
                return False
        except requests.RequestException as exc:
            log.error("HTTP error updating %s / %s: %s", submodel_id, element_id_short, exc)
            return False

    def ensure_submodel_element_exists(
        self,
        submodel_id: str,
        element_id_short: str,
        value,
    ) -> bool:
        """
        PUT a submodel element if it doesn't exist yet (first-time creation).
        This is called when PATCH returns 404.
        """
        sm_encoded = _b64url(submodel_id)
        url = f"{self.repo}/submodels/{sm_encoded}/submodel-elements"

        body = {
            "modelType": "Property",
            "idShort": element_id_short,
            "valueType": _xs_type(value),
            "value": str(value) if not isinstance(value, (bool, int, float)) else value,
        }

        try:
            resp = self._session.post(
                url,
                json=body,
                timeout=5,
            )
            if resp.status_code in (200, 201):
                log.info("Created element %s / %s", submodel_id, element_id_short)
                return True
            else:
                log.warning(
                    "Could not create element %s / %s: %s %s",
                    submodel_id, element_id_short, resp.status_code, resp.text[:200]
                )
                return False
        except requests.RequestException as exc:
            log.error("HTTP error creating %s / %s: %s", submodel_id, element_id_short, exc)
            return False

    def upsert(self, submodel_id: str, element_id_short: str, value):
        """PATCH element value; log a warning if the element or submodel is missing."""
        sm_encoded = _b64url(submodel_id)
        patch_url = f"{self.repo}/submodels/{sm_encoded}/submodel-elements/{element_id_short}/$value"
        if isinstance(value, bool):
            formatted = "true" if value else "false"
        else:
            formatted = str(value)
        body = json.dumps(formatted)

        try:
            resp = self._session.patch(
                patch_url,
                data=body,
                headers={"Content-Type": "application/json"},
                timeout=5,
            )
            if resp.status_code == 404:
                log.warning(
                    "Submodel or element not found in BaSyx: %s / %s — "
                    "load executor AAS files into BaSyx and restart aas-env",
                    submodel_id, element_id_short,
                )
            elif resp.status_code not in (200, 204):
                log.warning("PATCH %s returned %s", patch_url, resp.status_code)
        except requests.RequestException as exc:
            log.error("upsert error: %s", exc)


# ---------------------------------------------------------------------------
# AAS Consumer
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import load_config


# Mapping of VO property names to T4.2 Characteristic slug suffixes
CHARACTERISTIC_PROP_SLUGS: dict[str, str] = {
    "OperationalState": "operational_state",
    "GripperClose":     "gripper_close",
    "ToolAttach":       "tool_attach",
    "AirErrorCode":     "air_error_code",
    "PowerToolEnable":  "power_tool_enable",
    "Position":         "position",
    "EmergencyStop":    "emergency_stop",
    "ErrorCode":        "error_code",
    "SlotEmpty":        "slot_empty",
    "SlotPositions":    "slot_positions",
    "BatteryLevel":     "battery_level",
    "CurrentLocation":  "current_location",
    "PositionX":        "position_x",
    "PositionY":        "position_y",
}


class AASConsumer:
    """
    Consumes VO property-update events from Kafka and writes them to BaSyx.
    Updates both the Device `livedata` submodel and any corresponding T4.2
    `sc:Characteristic` submodel (`CharacteristicValue` + property element).
    """

    def __init__(self, config_path: str):
        self.cfg = load_config(config_path)

        self._updater = BaSyxUpdater(self.cfg["basyx"]["aas_repo"])

    def _wait_for_kafka(self, bootstrap_servers: str, retries: int = 10, delay: float = 5.0):
        for attempt in range(retries):
            try:
                consumer = KafkaConsumer(
                    bootstrap_servers=bootstrap_servers,
                    consumer_timeout_ms=1000,
                )
                consumer.close()
                return True
            except Exception:
                log.warning(
                    "Kafka not available (attempt %d/%d), retrying in %.0fs ...",
                    attempt + 1, retries, delay
                )
                time.sleep(delay)
        return False

    def run(self):
        if not KAFKA_AVAILABLE:
            log.error("kafka-python is not installed. Run: pip install kafka-python")
            sys.exit(1)

        kafka_cfg = self.cfg["kafka"]
        bootstrap = kafka_cfg["bootstrap_servers"]
        topic = kafka_cfg["topic"]

        log.info("Waiting for Kafka broker at %s ...", bootstrap)
        if not self._wait_for_kafka(bootstrap):
            log.error("Kafka broker unreachable after retries — exiting")
            sys.exit(1)

        consumer = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap,
            group_id="infraflex-aas-consumer",
            auto_offset_reset="latest",
            enable_auto_commit=True,
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            session_timeout_ms=30000,
            heartbeat_interval_ms=10000,
        )

        log.info("AAS Consumer listening on topic '%s' ...", topic)

        try:
            for msg in consumer:
                event = msg.value
                try:
                    self._handle_event(event)
                except Exception as exc:
                    log.error("Error handling event %s: %s", event, exc)
        except KeyboardInterrupt:
            log.info("Shutting down AAS Consumer")
        finally:
            consumer.close()

    def _handle_event(self, event: dict):
        submodel_id = event.get("aas_submodel_id")
        shell_id = event.get("aas_shell_id", "")
        prop_name = event.get("property")
        value = event.get("value")
        vo_id = event.get("vo_id", "?")

        if not submodel_id or not prop_name or value is None:
            log.debug("Skipping incomplete event: %s", event)
            return

        log.info(
            "%s → %s / %s = %r  (ts: %s)",
            vo_id, submodel_id, prop_name, value, event.get("timestamp", "")
        )
        # 1. Update Device LiveData submodel
        self._updater.upsert(submodel_id, prop_name, value)

        # 2. Update corresponding T4.2 CharacteristicSubmodel if mapped
        char_suffix = CHARACTERISTIC_PROP_SLUGS.get(prop_name)
        if char_suffix:
            dev_slug = shell_id.split(":")[-1] if shell_id else vo_id.lower()
            char_sm_id = (
                f"urn:aas:infraflex:characteristic:char_{dev_slug}_{char_suffix}"
                f":sm:characteristicsubmodel"
            )
            self._updater.upsert(char_sm_id, "CharacteristicValue", value)
            self._updater.upsert(char_sm_id, prop_name, value)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Infraflex AAS Consumer")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent.parent / "config" / "devices.json"),
        help="Path to devices.json configuration file",
    )
    args = parser.parse_args()

    consumer = AASConsumer(args.config)
    consumer.run()


if __name__ == "__main__":
    main()
