#!/usr/bin/env python3
"""
VO Manager — the MQTT-to-WoT-to-Kafka bridge.

Flow per device:
  MQTT Infraflex/Executors/{path}/{Data|Status|Task}
    -> parse payload
    -> update ExposedThing properties (wotpy2)
    -> on property change -> publish to Kafka topic

Run:
  python vo_manager.py [--config path/to/devices.json]
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Optional Kafka import — falls back to logging if kafka-python is not installed
# ---------------------------------------------------------------------------
try:
    from kafka import KafkaProducer
    KAFKA_AVAILABLE = True
except Exception as _kafka_err:
    KAFKA_AVAILABLE = False
    logging.warning(
        "kafka-python could not be imported (%s: %s) — "
        "property updates will be logged only. "
        "Ensure the venv is active: source ../.venv/bin/activate",
        type(_kafka_err).__name__, _kafka_err,
    )

# ---------------------------------------------------------------------------
# Optional requests import — needed for auto-registering new devices in BaSyx
# ---------------------------------------------------------------------------
try:
    import requests as _requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ---------------------------------------------------------------------------
# wotpy2 / vo-wot imports
# ---------------------------------------------------------------------------
from wotpy2.wot.servient import Servient
from wotpy2.wot.wot import WoT

# ---------------------------------------------------------------------------
# Local imports
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import load_config
from vo_layer.thing_descriptions import get_td

try:
    from ontology_bridge.generate_aas import executor_aas as _executor_aas
    _GENERATE_AAS_AVAILABLE = True
except Exception:
    _GENERATE_AAS_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("vo_manager")


# ---------------------------------------------------------------------------
# Helpers to extract nested MQTT payload fields
# ---------------------------------------------------------------------------

def _extract(data: dict, dot_path: str) -> Any:
    """Extract a value from a nested dict using dot notation (e.g. 'air.gripper_close')."""
    parts = dot_path.split(".")
    val = data
    for p in parts:
        if not isinstance(val, dict):
            return None
        val = val.get(p)
    return val


PROPERTY_EXTRACTORS = {
    "PowerAir": {
        "Data": lambda d: {
            "GripperClose":   _extract(d, "air.gripper_close"),
            "ToolAttach":     _extract(d, "air.tool_attach"),
            "AirErrorCode":   _extract(d, "air.error_code"),
            "AirErrorMsg":    _extract(d, "air.error_msg"),
            "PowerToolEnable": _extract(d, "power.tool_enable"),
        },
        "Status": lambda d: {
            "OperationalState": d.get("state"),
        },
        "Task": lambda d: {
            "CurrentSkill": d.get("skill"),
            "TaskState":    d.get("state"),
            "TaskDuration": d.get("duration"),
        },
    },
    "Lift": {
        "Data": lambda d: {
            "Position":      d.get("position"),
            "ServiceMode":   d.get("service"),
            "ErrorCode":     d.get("error_code"),
            "ErrorMsg":      d.get("error_msg"),
            "ControlStop":   d.get("c_stop"),
            "EmergencyStop": d.get("e_stop"),
            "LoweringEnable": d.get("lowering_enable"),
        },
        "Status": lambda d: {
            "OperationalState": d.get("state"),
        },
        "Task": lambda d: {
            "CurrentSkill": d.get("skill"),
            "TaskState":    d.get("state"),
        },
    },
    "Turntable": {
        "Data": lambda d: {
            "SlotEmpty":     json.dumps(d.get("slot_empty")) if d.get("slot_empty") is not None else None,
            "SlotPositions": json.dumps(d.get("slot_positions")) if d.get("slot_positions") is not None else None,
            "ErrorCode":     d.get("error_code"),
            "ErrorMsg":      d.get("error_msg"),
        },
        "Status": lambda d: {
            "OperationalState": d.get("state"),
        },
        "Task": lambda d: {
            "CurrentSkill": d.get("skill"),
            "TaskState":    d.get("state"),
        },
    },
    "AMR": {
        "Data": lambda d: {
            "OperationalState": d.get("state"),
            "BatteryLevel":     d.get("battery"),
            "CurrentLocation":  d.get("location"),
            "PositionX":        _extract(d, "position.x"),
            "PositionY":        _extract(d, "position.y"),
            "Orientation":      _extract(d, "position.orientation"),
            "ErrorCode":        d.get("error_code"),
            "ErrorMsg":         d.get("error_msg"),
        },
        "Status": lambda d: {
            "OperationalState": d.get("state"),
        },
        "Task": lambda d: {
            "CurrentSkill": d.get("skill"),
            "TaskState":    d.get("state"),
        },
    },
    "Warehouse": {
        "Data": lambda d: {
            "ErrorCode":     d.get("error_code"),
            "ErrorMsg":      d.get("error_msg"),
            "EmergencyStop": d.get("e_stop"),
            "IOStatus":      d.get("v24_io_status"),
        },
        "Status": lambda d: {
            "OperationalState": d.get("state"),
        },
        "Task": lambda d: {
            "CurrentSkill": d.get("skill"),
            "TaskState":    d.get("state"),
        },
    },
    "Debonder": {
        "Data": lambda d: {
            "ErrorCode": d.get("error_code"),
            "ErrorMsg":  d.get("error_msg"),
        },
        "Status": lambda d: {
            "OperationalState": d.get("state"),
        },
        "Task": lambda d: {
            "CurrentSkill": d.get("skill"),
            "TaskState":    d.get("state"),
        },
    },
}


# ---------------------------------------------------------------------------
# Wildcard device discovery helpers
# ---------------------------------------------------------------------------

_KNOWN_TYPES = set(PROPERTY_EXTRACTORS.keys())


def _infer_device_type(device_path: str) -> str | None:
    """Infer executor type from an MQTT device_path.

    Handles two layouts:
      Cell{N}/PowerAir  → last segment is the type
      AMR/MIR200_1      → first segment prefix matches "AMR"
      Warehouse003      → single segment starts with "Warehouse"
    """
    parts = device_path.split("/")
    if parts[-1] in _KNOWN_TYPES:
        return parts[-1]
    first = parts[0]
    for dtype in _KNOWN_TYPES:
        if first == dtype or first.startswith(dtype):
            return dtype
    return None


def _derive_device_ids(device_path: str) -> tuple[str, str, str]:
    """Derive (vo_id, aas_shell_id, aas_submodel_id) from an MQTT device_path following T4.2 conventions.

    Examples:
      Cell3/PowerAir  → Cell3_PowerAir, urn:unimaas:infraflex:device:cell3_powerair, …:sm:livedata
      AMR/MIR200_1   → AMR_MIR200_1,   urn:unimaas:infraflex:device:amr_mir200_1,  …:sm:livedata
    """
    vo_id = device_path.replace("/", "_")
    slug = device_path.lower().replace("/", "_").replace("-", "_")
    return vo_id, f"urn:unimaas:infraflex:device:{slug}", f"urn:aas:infraflex:device:{slug}:sm:livedata"


# ---------------------------------------------------------------------------
# Kafka producer wrapper
# ---------------------------------------------------------------------------

class KafkaPublisher:
    """Thread-safe Kafka publisher for VO property update events."""

    def __init__(self, bootstrap_servers: str, topic: str):
        self.topic = topic
        self._producer = None
        if KAFKA_AVAILABLE:
            try:
                self._producer = KafkaProducer(
                    bootstrap_servers=bootstrap_servers,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    key_serializer=lambda k: k.encode("utf-8") if k else None,
                    acks="all",
                    retries=3,
                )
                log.info("Kafka producer connected to %s", bootstrap_servers)
            except Exception as _ke:
                log.warning("Kafka broker unavailable at %s (%s) — running without Kafka", bootstrap_servers, _ke)

    def publish(self, vo_id: str, device_path: str, device_type: str,
                aas_shell_id: str, aas_submodel_id: str,
                prop_name: str, value: Any, timestamp: str):
        event = {
            "vo_id": vo_id,
            "device_path": device_path,
            "device_type": device_type,
            "aas_shell_id": aas_shell_id,
            "aas_submodel_id": aas_submodel_id,
            "property": prop_name,
            "value": value,
            "timestamp": timestamp,
        }
        if self._producer is not None:
            self._producer.send(self.topic, key=vo_id, value=event)
        else:
            log.debug("Kafka not available — event: %s", event)

    def flush(self):
        if self._producer is not None:
            self._producer.flush()

    def close(self):
        if self._producer is not None:
            self._producer.close()


# ---------------------------------------------------------------------------
# VO state store (in-memory, thread-safe)
# ---------------------------------------------------------------------------

class VOState:
    """Holds the current property values for one Virtual Object."""

    def __init__(self, vo_id: str, device_path: str, device_type: str,
                 aas_shell_id: str, aas_submodel_id: str):
        self.vo_id = vo_id
        self.device_path = device_path
        self.device_type = device_type
        self.aas_shell_id = aas_shell_id
        self.aas_submodel_id = aas_submodel_id
        self._props: dict[str, Any] = {}
        self._pending: dict[str, Any] = {}
        self._lock = threading.Lock()

    def update(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Update properties; return dict of changed {name: value} and mark them pending."""
        changed = {}
        with self._lock:
            for name, value in updates.items():
                if value is None:
                    continue
                if self._props.get(name) != value:
                    self._props[name] = value
                    self._pending[name] = value
                    changed[name] = value
        return changed

    def flush_pending(self) -> dict[str, Any]:
        """Return and clear all changes accumulated since the last flush."""
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()
            return pending

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._props)


# ---------------------------------------------------------------------------
# Main VOManager
# ---------------------------------------------------------------------------

class VOManager:
    """
    Manages all Virtual Objects for Infraflex executors.

    1. Loads device config from devices.json.
    2. Creates a wotpy2 ExposedThing per device.
    3. Bridges MQTT messages into VO property updates.
    4. Publishes changed properties to Kafka.
    """

    def __init__(self, config_path: str):
        self.cfg = load_config(config_path)

        self._vo_states: dict[str, VOState] = {}
        # MQTT path → VOState mapping
        self._path_to_vo: dict[str, VOState] = {}
        # wotpy2 objects
        self._servient = Servient()
        self._wot = WoT(servient=self._servient)
        self._exposed_things: dict[str, Any] = {}
        # Queue for passing MQTT messages to the asyncio loop
        self._mqtt_queue: asyncio.Queue = None
        self._kafka = KafkaPublisher(
            bootstrap_servers=self.cfg["kafka"]["bootstrap_servers"],
            topic=self.cfg["kafka"]["topic"],
        )
        self._basyx_url = self.cfg.get("basyx", {}).get("aas_repo", "").rstrip("/")
        # Tracks paths currently being registered to prevent duplicate concurrent creation
        self._registering: set[str] = set()

    # ------------------------------------------------------------------
    # wotpy2 Thing creation (async)
    # ------------------------------------------------------------------

    async def _create_things(self):
        for device in self.cfg["devices"]:
            vo_id = device["vo_id"]
            path = device["mqtt_path"]
            dtype = device["type"]

            td = get_td(dtype, vo_id, path)
            try:
                exposed = self._wot.produce(json.dumps(td))
                exposed.expose()
                self._exposed_things[vo_id] = exposed
                log.info("Created WoT ExposedThing: %s (%s)", vo_id, dtype)
            except Exception as exc:
                log.error("Failed to create ExposedThing for %s: %s", vo_id, exc)

            state = VOState(
                vo_id=vo_id,
                device_path=path,
                device_type=dtype,
                aas_shell_id=device["aas_shell_id"],
                aas_submodel_id=device["aas_submodel_id"],
            )
            self._vo_states[vo_id] = state
            self._path_to_vo[path] = state

    # ------------------------------------------------------------------
    # Dynamic device registration
    # ------------------------------------------------------------------

    def _post_executor_to_basyx(self, vo_id: str, aas_shell_id: str,
                                 aas_submodel_id: str, device_type: str,
                                 device_path: str):
        """POST a new executor shell + submodel to BaSyx (runs in a thread pool)."""
        if not _GENERATE_AAS_AVAILABLE or not REQUESTS_AVAILABLE or not self._basyx_url:
            return
        try:
            bundle = _executor_aas(aas_shell_id, aas_submodel_id, vo_id, device_type, device_path)
            shell = bundle["assetAdministrationShells"][0]
            submodel = bundle["submodels"][0]

            for url, body, label in [
                (f"{self._basyx_url}/shells", shell, "shell"),
                (f"{self._basyx_url}/submodels", submodel, "submodel"),
            ]:
                r = _requests.post(url, json=body, timeout=10)
                if r.status_code in (200, 201):
                    log.info("BaSyx: registered %s for %s", label, vo_id)
                elif r.status_code == 409:
                    log.debug("BaSyx: %s %s already exists", label, vo_id)
                else:
                    log.warning("BaSyx: failed to register %s for %s: HTTP %s",
                                label, vo_id, r.status_code)
        except Exception as exc:
            log.warning("BaSyx: error registering %s: %s", vo_id, exc)

    async def _auto_register_device(self, device_path: str, device_type: str):
        """Create a VOState + WoT ExposedThing for a device seen for the first time."""
        self._registering.add(device_path)
        try:
            vo_id, aas_shell_id, aas_submodel_id = _derive_device_ids(device_path)

            td = get_td(device_type, vo_id, device_path)
            try:
                exposed = self._wot.produce(json.dumps(td))
                exposed.expose()
                self._exposed_things[vo_id] = exposed
            except Exception as exc:
                log.error("Auto-register: ExposedThing creation failed for %s: %s", vo_id, exc)

            state = VOState(
                vo_id=vo_id,
                device_path=device_path,
                device_type=device_type,
                aas_shell_id=aas_shell_id,
                aas_submodel_id=aas_submodel_id,
            )
            self._vo_states[vo_id] = state
            self._path_to_vo[device_path] = state

            log.info("Auto-discovered device: %s (%s) → vo_id=%s", device_path, device_type, vo_id)

            # Fire-and-forget BaSyx registration (non-blocking)
            asyncio.get_running_loop().run_in_executor(
                None,
                self._post_executor_to_basyx,
                vo_id, aas_shell_id, aas_submodel_id, device_type, device_path,
            )
        finally:
            self._registering.discard(device_path)

    # ------------------------------------------------------------------
    # MQTT handling
    # ------------------------------------------------------------------

    def _start_mqtt(self):
        mqtt_cfg = self.cfg["mqtt"]
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="infraflex-vo-manager")
        client.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])

        def on_connect(c, userdata, flags, reason_code, *_extra):
            rc = reason_code if isinstance(reason_code, int) else getattr(reason_code, "value", 0)
            if rc == 0:
                topic = f"{mqtt_cfg['base_topic']}/#"
                c.subscribe(topic, qos=0)
                log.info("MQTT connected — subscribed to %s", topic)
            else:
                log.error("MQTT connection failed: %s", reason_code)

        def on_message(c, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
            except Exception:
                return
            # Put into asyncio queue from the MQTT thread
            asyncio.run_coroutine_threadsafe(
                self._mqtt_queue.put((msg.topic, payload)),
                self._loop,
            )

        def on_disconnect(c, userdata, flags, reason_code=0, *_extra):
            rc = reason_code if isinstance(reason_code, int) else getattr(reason_code, "value", 0)
            if rc != 0:
                log.warning("MQTT disconnected unexpectedly (%s) — will reconnect", reason_code)

        client.on_connect = on_connect
        client.on_message = on_message
        client.on_disconnect = on_disconnect
        client.connect_async(mqtt_cfg["host"], mqtt_cfg["port"], keepalive=60)
        client.loop_forever(retry_first_connection=True)

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    def _parse_topic(self, topic: str) -> tuple[str | None, str | None]:
        """
        Extract (device_path, message_type) from an Infraflex MQTT topic.

        Example: "Infraflex/Executors/Cell2/PowerAir/Data"
            -> ("Cell2/PowerAir", "Data")
        """
        base = self.cfg["mqtt"]["base_topic"]  # e.g. "Infraflex/Executors"
        if not topic.startswith(base + "/"):
            return None, None
        remainder = topic[len(base) + 1:]
        parts = remainder.rsplit("/", 1)
        if len(parts) != 2:
            return None, None
        device_path, msg_type = parts
        return device_path, msg_type

    async def _process_messages(self):
        while True:
            topic, payload = await self._mqtt_queue.get()
            device_path, msg_type = self._parse_topic(topic)
            if device_path is None or msg_type not in ("Data", "Status", "Task"):
                continue

            vo_state = self._path_to_vo.get(device_path)
            if vo_state is None:
                # Skip if registration is already in progress for this path
                if device_path in self._registering:
                    continue
                device_type = _infer_device_type(device_path)
                if device_type is None:
                    log.debug("Skipping message from unrecognised path: %s", device_path)
                    continue
                await self._auto_register_device(device_path, device_type)
                vo_state = self._path_to_vo.get(device_path)
                if vo_state is None:
                    continue

            extractors = PROPERTY_EXTRACTORS.get(vo_state.device_type, {})
            extractor = extractors.get(msg_type)
            if extractor is None:
                continue

            updates = extractor(payload)
            timestamp = payload.get("_meta", {}).get("timestamp", "")
            if timestamp:
                updates["LastUpdate"] = timestamp

            changed = vo_state.update(updates)
            if not changed:
                continue

            # Update wotpy2 ExposedThing
            exposed = self._exposed_things.get(vo_state.vo_id)
            if exposed is not None:
                for prop_name, value in changed.items():
                    try:
                        prop_val = value if not isinstance(value, bool) else value
                        await exposed.write_property(prop_name, prop_val)
                    except Exception as exc:
                        log.debug("write_property %s.%s failed: %s", vo_state.vo_id, prop_name, exc)

            log.debug("%s: %d properties changed", vo_state.vo_id, len(changed))

    async def _flush_loop(self, interval: float = 3.0):
        """Publish accumulated VO property changes to Kafka every `interval` seconds."""
        while True:
            await asyncio.sleep(interval)
            ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            for vo_state in list(self._vo_states.values()):
                pending = vo_state.flush_pending()
                for prop_name, value in pending.items():
                    self._kafka.publish(
                        vo_id=vo_state.vo_id,
                        device_path=vo_state.device_path,
                        device_type=vo_state.device_type,
                        aas_shell_id=vo_state.aas_shell_id,
                        aas_submodel_id=vo_state.aas_submodel_id,
                        prop_name=prop_name,
                        value=value,
                        timestamp=ts,
                    )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self):
        self._loop = asyncio.get_running_loop()
        self._mqtt_queue = asyncio.Queue()

        log.info("Creating WoT ExposedThings ...")
        await self._create_things()

        log.info("Starting MQTT client thread ...")
        mqtt_thread = threading.Thread(target=self._start_mqtt, daemon=True)
        mqtt_thread.start()

        log.info("VO Manager running — press Ctrl+C to stop")
        flush_task = asyncio.create_task(self._flush_loop(interval=3.0))
        try:
            await self._process_messages()
        finally:
            flush_task.cancel()
            self._kafka.flush()
            self._kafka.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Infraflex VO Manager")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent.parent / "config" / "devices.json"),
        help="Path to devices.json configuration file",
    )
    args = parser.parse_args()

    manager = VOManager(args.config)

    loop = asyncio.new_event_loop()
    main_task: asyncio.Task | None = None

    def _handle_signal():
        log.info("Shutdown signal received")
        if main_task is not None:
            main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    try:
        main_task = loop.create_task(manager.run())
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        pass  # clean shutdown via signal
    finally:
        loop.close()


if __name__ == "__main__":
    main()
