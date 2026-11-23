#!/usr/bin/env python3
"""
Infraflex BaSyx AAS → Ontotext GraphDB Knowledge Graph Synchronizer (UniMaaS MSC Ontology).

Polls Eclipse BaSyx (:8081) every 3 seconds, reads all AAS shells and submodels,
and synchronizes them into Ontotext GraphDB (:7200) using the UniMaaS Manufacturing
Service Chain (MSC) Ontology (`http://unimaas-project.eu/MSCOntology#`).

On startup:
  1. Ensures the target GraphDB repository (`infraflex`) exists.
  2. Loads `ontologies/MSC-ontology-1.2.0.ttl` and `ontologies/next-steps.ttl` into
     the repository so all MSC classes and object/data property definitions are present.

Mapped MSC Classes (`@prefix sc: <http://unimaas-project.eu/MSCOntology#>`):
  • sc:Site                  (Cells: cell1..cell6)
  • sc:Device                (Executors: PowerAir, Lift, Turntable, AMR, Warehouse, Debonder)
  • sc:EquipmentResource     (Robot Arms, Tools, Add-ons)
  • sc:Sensor                (Device telemetry sensors)
  • sc:Characteristic        (Device & site characteristics)
  • sc:Product               (Flashlight & component products)
  • sc:Process               (Manufacturing processes)
  • sc:ProcessConfiguration  (Cell-specific process configurations & steps)
  • sc:LogisticRoute         (Inter-cell & warehouse transit routes)

Run:
  cd unimaas_infraflex/kg
  python infraflex2kg.py [--config ../config/devices.json] [--interval 3]
"""

import argparse
import base64
from datetime import datetime, timezone
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

try:
    from kg.graphdb_client import GraphDBClient
except ImportError:
    try:
        from unimaas_infraflex.kg.graphdb_client import GraphDBClient
    except ImportError:
        from graphdb_client import GraphDBClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("infraflex2kg")

MSC_NS = "http://unimaas-project.eu/MSCOntology#"
INST_NS = "http://unimaas-project.eu/infraflex/instances#"
REPO_ROOT = Path(__file__).resolve().parents[2]

PREFIXES = f"""
PREFIX sc: <{MSC_NS}>
PREFIX inst: <{INST_NS}>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""

IDSHORT_TO_SC_PROP = {
    "SiteIdentifier": "sc:id",
    "SensorIdentifier": "sc:id",
    "CharacteristicIdentifier": "sc:id",
    "ProductIdentifier": "sc:id",
    "ProcessIdentifier": "sc:id",
    "ConfigurationIdentifier": "sc:id",
    "RouteIdentifier": "sc:id",
    "ResourceIdentifier": "sc:id",
    "SerialNumber": "sc:id",
    "SiteName": "sc:name",
    "SensorName": "sc:name",
    "ProductName": "sc:name",
    "ProcessName": "sc:name",
    "ConfigurationName": "sc:name",
    "RouteName": "sc:name",
    "ResourceName": "sc:name",
    "ManufacturerProductDesignation": "sc:name",
    "ResourceType": "sc:resourceType",
    "ProcessType": "sc:processType",
    "SensorType": "sc:sensorType",
    "ManufacturerName": "sc:manufacturer",
    "LocationAddress": "sc:locationAddress",
    "LocationAspectDesignation": "sc:locationAspectDesignation",
    "ProductAspectDesignation": "sc:productAspectDesignation",
    "CharacteristicValue": "sc:characteristicValue",
    "DataType": "sc:dataType",
    "Unit": "sc:unit",
    "UnitOfMeasure": "sc:unit",
    "AvailableQuantity": "sc:availableQuantity",
    "SamplingRate": "sc:samplingRate",
    "RouteStatus": "sc:routeStatus",
    "TransportationMode": "sc:transportationMode",
    "TransmissionUnit": "sc:transmissionUnit",
    "TransmissionCost": "sc:transmissionCost",
    "OperationalState": "sc:operationalState",
    "CurrentSkill": "sc:currentSkill",
    "TaskState": "sc:taskState",
    "TaskDuration": "sc:taskDuration",
    "GripperClose": "sc:gripperClose",
    "ToolAttach": "sc:toolAttach",
    "PowerToolEnable": "sc:powerToolEnable",
    "AirErrorCode": "sc:airErrorCode",
    "AirErrorMsg": "sc:airErrorMsg",
    "ErrorCode": "sc:errorCode",
    "ErrorMsg": "sc:errorMsg",
    "Position": "sc:position",
    "ServiceMode": "sc:serviceMode",
    "ControlStop": "sc:controlStop",
    "EmergencyStop": "sc:emergencyStop",
    "LoweringEnable": "sc:loweringEnable",
    "SlotEmpty": "sc:slotEmpty",
    "SlotPositions": "sc:slotPositions",
    "BatteryLevel": "sc:batteryLevel",
    "CurrentLocation": "sc:currentLocation",
    "PositionX": "sc:positionX",
    "PositionY": "sc:positionY",
    "Orientation": "sc:orientation",
    "IOStatus": "sc:ioStatus",
}


def _safe_local_name(identifier: str) -> str:
    s = str(identifier).strip()
    for prefix in (
        "urn:unimaas:infraflex:",
        "urn:aas:infraflex:",
        "urn:infraflex:aas:",
        "urn:asset:infraflex:",
        "https://example.com/aas/",
    ):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.replace(":", "_")
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", s).strip("_") or "entity"


def _shell_uri(shell_id: str) -> str:
    return f"{INST_NS}{_safe_local_name(shell_id)}"


def _safe_prop(name: str) -> str:
    if name in IDSHORT_TO_SC_PROP:
        return IDSHORT_TO_SC_PROP[name]
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "", str(name))
    if not cleaned:
        return "sc:property"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    else:
        cleaned = cleaned[0].lower() + cleaned[1:]
    return f"sc:{cleaned}"


def _format_literal(val: Any, value_type: str = "") -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    vt = (value_type or "").lower()
    if isinstance(val, bool) or vt in ("xs:boolean", "boolean"):
        b = "true" if s.lower() in ("true", "1", "yes") else "false"
        return f'"{b}"^^xsd:boolean'
    if isinstance(val, int) or vt in ("xs:int", "xs:integer", "xs:long", "int", "integer"):
        try:
            return f'"{int(float(s))}"^^xsd:integer'
        except ValueError:
            pass
    if isinstance(val, float) or vt in ("xs:double", "xs:float", "xs:decimal", "double", "float", "decimal"):
        try:
            return f'"{float(s)}"^^xsd:decimal'
        except ValueError:
            pass
    esc = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", "")
    return f'"{esc}"'


def _semantic_id(el: dict) -> str:
    try:
        keys = el.get("semanticId", {}).get("keys", [])
        return keys[0].get("value", "") if keys else ""
    except Exception:
        return ""


def _resolve_msc_class(shell: dict) -> str:
    sem = _semantic_id(shell)
    if sem.startswith(MSC_NS):
        local = sem[len(MSC_NS):]
        if local:
            return f"sc:{local}"
    sid = shell.get("id", "").lower()
    if ":site:" in sid or ":cell:" in sid:
        return "sc:Site"
    if ":device:" in sid:
        return "sc:Device"
    if ":sensor:" in sid:
        return "sc:Sensor"
    if ":characteristic:" in sid:
        return "sc:Characteristic"
    if ":product:" in sid:
        return "sc:Product"
    if ":processconfiguration:" in sid:
        return "sc:ProcessConfiguration"
    if ":process:" in sid:
        return "sc:Process"
    if ":logisticroute:" in sid:
        return "sc:LogisticRoute"
    return "sc:EquipmentResource"


class BaSyxClient:
    """Minimal BaSyx AAS Repository REST client."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._s = requests.Session()
        self._s.headers["Accept"] = "application/json"

    def _b64(self, value: str) -> str:
        return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")

    def get_all_shells(self) -> list[dict]:
        all_shells = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            try:
                r = self._s.get(f"{self.base_url}/shells", params=params, timeout=10)
                r.raise_for_status()
                data = r.json()
                results = data.get("result", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                all_shells.extend(results)
                meta = data.get("paging_metadata", {}) if isinstance(data, dict) else {}
                cursor = meta.get("cursor")
                if not cursor or not results:
                    break
            except Exception as exc:
                log.error("Failed to list shells: %s", exc)
                break
        return all_shells

    def get_submodels_for_shell(self, shell: dict) -> list[dict]:
        submodels = []
        for ref in shell.get("submodels", []):
            sm_id = ref.get("keys", [{}])[0].get("value")
            if sm_id:
                sm = self._get_submodel(sm_id)
                if sm:
                    submodels.append(sm)
        return submodels

    def _get_submodel(self, submodel_id: str) -> dict | None:
        try:
            r = self._s.get(f"{self.base_url}/submodels/{self._b64(submodel_id)}", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None


class InfraflexSynchronizer:
    """Polls BaSyx and keeps the GraphDB MSC knowledge graph in sync."""

    def __init__(self, basyx_url: str, graphdb_cfg: dict):
        self.basyx = BaSyxClient(basyx_url)
        self.client = GraphDBClient(
            base_url=graphdb_cfg.get("url", "http://localhost:7200"),
            repository=graphdb_cfg.get("repository", "infraflex"),
            user=graphdb_cfg.get("user"),
            password=graphdb_cfg.get("password"),
        )
        self._schema_loaded = False

    def close(self):
        self.client.close()

    def ensure_schema(self):
        self.client.ensure_repository()
        if self._schema_loaded:
            return
        for ttl_name in ("MSC-ontology-1.2.0.ttl", "next-steps.ttl"):
            ttl_path = REPO_ROOT / "ontologies" / ttl_name
            if ttl_path.is_file():
                try:
                    ttl_data = ttl_path.read_text(encoding="utf-8")
                    resp = self.client._session.post(
                        f"{self.client.base_url}/repositories/{self.client.repository}/statements",
                        data=ttl_data.encode("utf-8"),
                        headers={"Content-Type": "text/turtle"},
                        timeout=15,
                    )
                    if resp.status_code in (200, 204):
                        log.info("Loaded MSC schema %s into GraphDB repository '%s'.", ttl_name, self.client.repository)
                except Exception as exc:
                    log.warning("Could not preload %s into GraphDB: %s", ttl_name, exc)
        self._schema_loaded = True

    def _extract_triples_from_elements(
        self,
        subj_uri: str,
        elements: list[dict],
        data_props: dict[str, str],
        obj_triples: list[str],
        parent_sem: str = "",
        parent_idshort: str = "",
    ):
        for el in elements:
            if not isinstance(el, dict):
                continue
            mt = el.get("modelType", "")
            id_short = el.get("idShort", "")
            sem = _semantic_id(el) or parent_sem

            if mt == "Property":
                lit = _format_literal(el.get("value"), el.get("valueType", ""))
                if lit is None:
                    continue
                pred = _safe_prop(id_short)
                data_props[pred] = lit

            elif mt == "MultiLanguageProperty":
                vals = el.get("value", [])
                text = next((v.get("text", "") for v in vals if isinstance(v, dict) and v.get("language") == "en"), "")
                if not text and vals and isinstance(vals[0], dict):
                    text = vals[0].get("text", "")
                lit = _format_literal(text)
                if lit:
                    data_props[_safe_prop(id_short)] = lit

            elif mt == "ReferenceElement":
                keys = el.get("value", {}).get("keys", [])
                if not keys:
                    continue
                target_id = keys[0].get("value", "")
                if not target_id:
                    continue
                target_uri = _shell_uri(target_id)
                self._append_ref_triples(subj_uri, target_uri, sem, id_short, parent_idshort, obj_triples)

            elif mt in ("SubmodelElementCollection", "SubmodelElementList"):
                children = el.get("value", [])
                if isinstance(children, list):
                    self._extract_triples_from_elements(
                        subj_uri, children, data_props, obj_triples, parent_sem=sem, parent_idshort=id_short
                    )

    @staticmethod
    def _append_ref_triples(
        subj_uri: str,
        target_uri: str,
        sem: str,
        id_short: str,
        parent_idshort: str,
        obj_triples: list[str],
    ):
        if sem.startswith(MSC_NS):
            local = sem[len(MSC_NS):]
            if local == "hasDevice_inverse":
                obj_triples.append(f"<{target_uri}> sc:hasDevice <{subj_uri}> .")
                return
            if local == "hasCharacteristic_inverse":
                obj_triples.append(f"<{target_uri}> sc:hasCharacteristic <{subj_uri}> .")
                return
            if local == "observes":
                obj_triples.append(f"<{subj_uri}> sc:observes <{target_uri}> .")
                obj_triples.append(f"<{target_uri}> sc:isObservedBy <{subj_uri}> .")
                return
            if local == "isObservedBy":
                obj_triples.append(f"<{subj_uri}> sc:isObservedBy <{target_uri}> .")
                obj_triples.append(f"<{target_uri}> sc:observes <{subj_uri}> .")
                return
            if local == "isPerformedAt":
                obj_triples.append(f"<{subj_uri}> sc:isPerformedAt <{target_uri}> .")
                obj_triples.append(f"<{target_uri}> sc:performs <{subj_uri}> .")
                return
            if local == "performs":
                obj_triples.append(f"<{subj_uri}> sc:performs <{target_uri}> .")
                obj_triples.append(f"<{target_uri}> sc:isPerformedAt <{subj_uri}> .")
                return
            obj_triples.append(f"<{subj_uri}> sc:{local} <{target_uri}> .")
            return

        combined = f"{parent_idshort}/{id_short}".lower()
        if "device" in combined or "robotarm" in combined:
            obj_triples.append(f"<{subj_uri}> sc:hasDevice <{target_uri}> .")
        elif "characteristic" in combined:
            obj_triples.append(f"<{subj_uri}> sc:hasCharacteristic <{target_uri}> .")
        elif "observ" in combined:
            obj_triples.append(f"<{subj_uri}> sc:observes <{target_uri}> .")
        elif "component" in combined:
            obj_triples.append(f"<{subj_uri}> sc:hasComponent <{target_uri}> .")
        elif "process" in combined:
            obj_triples.append(f"<{subj_uri}> sc:requiresProcess <{target_uri}> .")
        elif "start" in combined:
            obj_triples.append(f"<{subj_uri}> sc:hasStartingPoint <{target_uri}> .")
        elif "end" in combined:
            obj_triples.append(f"<{subj_uri}> sc:hasEndingPoint <{target_uri}> .")
        elif "cell" in combined or "site" in combined:
            obj_triples.append(f"<{subj_uri}> sc:isPerformedAt <{target_uri}> .")
        elif "resource" in combined:
            obj_triples.append(f"<{subj_uri}> sc:usesResource <{target_uri}> .")

    def sync_shell(self, shell: dict):
        shell_id = shell.get("id", "")
        if not shell_id:
            return
        id_short = shell.get("idShort", shell_id)
        msc_class = _resolve_msc_class(shell)
        subj_uri = _shell_uri(shell_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        data_props: dict[str, str] = {
            "sc:id": _format_literal(shell_id) or f'"{shell_id}"',
            "sc:name": _format_literal(id_short) or f'"{id_short}"',
            "rdfs:label": _format_literal(id_short) or f'"{id_short}"',
            "sc:lastUpdated": f'"{now_iso}"^^xsd:dateTime',
        }
        obj_triples: list[str] = []

        for sm in self.basyx.get_submodels_for_shell(shell):
            self._extract_triples_from_elements(
                subj_uri,
                sm.get("submodelElements", []),
                data_props,
                obj_triples,
                parent_sem=_semantic_id(sm),
                parent_idshort=sm.get("idShort", ""),
            )

        del_clauses = []
        del_opt = []
        ins_clauses = [f"<{subj_uri}> a owl:NamedIndividual, {msc_class} ."]

        for idx, (pred, lit) in enumerate(data_props.items()):
            var = f"?v{idx}"
            del_clauses.append(f"<{subj_uri}> {pred} {var} .")
            del_opt.append(f"OPTIONAL {{ <{subj_uri}> {pred} {var} }}")
            ins_clauses.append(f"<{subj_uri}> {pred} {lit} .")

        ins_clauses.extend(obj_triples)

        sparql = f"""{PREFIXES}
DELETE {{
    {" ".join(del_clauses)}
}}
INSERT {{
    {" ".join(ins_clauses)}
}}
WHERE {{
    {" ".join(del_opt)}
}}
"""
        self.client.update(sparql)

    def sync_once(self):
        shells = self.basyx.get_all_shells()
        synced = 0
        for shell in shells:
            try:
                self.sync_shell(shell)
                synced += 1
            except Exception as exc:
                log.error("Error syncing shell %s: %s", shell.get("id"), exc)
        log.debug("Synced %d AAS shells into MSC Knowledge Graph.", synced)

    def run(self, interval: int = 3):
        log.info("Infraflex → MSC GraphDB sync started (BaSyx=%s, interval=%ds)", self.basyx.base_url, interval)
        self.ensure_schema()
        try:
            while True:
                self.sync_once()
                time.sleep(interval)
        except KeyboardInterrupt:
            log.info("Stopped")
        finally:
            self.close()


def main():
    parser = argparse.ArgumentParser(description="Infraflex AAS → MSC Ontology GraphDB synchronizer")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent.parent / "config" / "devices.json"),
        help="Path to devices.json (default: ../config/devices.json)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3,
        help="BaSyx poll interval in seconds (default: 3)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single synchronization pass and exit",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from config import load_config

    cfg = load_config(args.config)
    graphdb_cfg = cfg.get("graphdb", {"url": "http://localhost:7200", "repository": "infraflex"})

    sync = InfraflexSynchronizer(
        basyx_url=cfg.get("basyx", {}).get("aas_repo", "http://localhost:8081"),
        graphdb_cfg=graphdb_cfg,
    )
    if args.once:
        sync.ensure_schema()
        sync.sync_once()
        sync.close()
    else:
        sync.run(args.interval)


if __name__ == "__main__":
    main()
