#!/usr/bin/env python3
"""
Ontology Bridge — AAS / BaSyx → UniMaaS MSC Ontology (http://unimaas-project.eu/MSCOntology#)

Reads all AAS V3 JSON (and AASX) definitions from AAS/ along with the base
UniMaaS Manufacturing Service Chain (MSC) Ontology (ontologies/MSC-ontology-1.2.0.ttl
and ontologies/next-steps.ttl), optionally overlays live telemetry from BaSyx
(:8081), and exports the complete MSC-based Infraflex ontology instance graph
to ontologies/instances/infraflex_instances.ttl.

Mapped MSC Classes (http://unimaas-project.eu/MSCOntology#):
  • sc:Site                  (AAS/cells/*.json)
  • sc:Device                (AAS/executors/*.json)
  • sc:EquipmentResource     (AAS/arms/*.json, AAS/tools/*.json, AAS/addons/*.json)
  • sc:Sensor                (AAS/sensors/*.json)
  • sc:Characteristic        (AAS/characteristics/*.json)
  • sc:Product               (AAS/products/*.json)
  • sc:Process               (AAS/processes/*.json)
  • sc:ProcessConfiguration  (AAS/processes/*_cell*.json)
  • sc:LogisticRoute         (AAS/routes/*.json)

Mapped MSC Object Properties:
  • sc:hasDevice             (Site -> Device / EquipmentResource)
  • sc:hasCharacteristic     (ConfigurableEntity -> Characteristic)
  • sc:observes              (Sensor -> Characteristic)
  • sc:isObservedBy          (Characteristic -> Sensor)
  • sc:hasComponent          (Product -> Product)
  • sc:requiresProcess       (Product / ProcessConfiguration -> Process)
  • sc:isPerformedAt         (Process / ProcessConfiguration -> Site)
  • sc:performs              (Site -> Process)
  • sc:usesResource          (ProcessConfiguration -> Resource)
  • sc:hasStartingPoint      (LogisticRoute -> Site / Location)
  • sc:hasEndingPoint        (LogisticRoute -> Site / Location)
  • sc:hasInput / sc:hasOutput / sc:hasNextStep / sc:hasPreviousStep

Run:
  python aas_to_ontology.py [--config ../config/devices.json] [--output ../../ontologies/instances/infraflex_instances.ttl]
"""

import argparse
import base64
import json
import logging
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore

try:
    from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD
except ImportError as exc:
    raise RuntimeError("rdflib is required: pip install rdflib") from exc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ontology_bridge")

MSC_URI = "http://unimaas-project.eu/MSCOntology#"
SC = Namespace(MSC_URI)
INST = Namespace("http://unimaas-project.eu/infraflex/instances#")
DCTERMS = Namespace("http://purl.org/dc/terms/")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MSC_ONTOLOGY = REPO_ROOT / "ontologies" / "MSC-ontology-1.2.0.ttl"
DEFAULT_NEXT_STEPS = REPO_ROOT / "ontologies" / "next-steps.ttl"
DEFAULT_OUTPUT_TTL = REPO_ROOT / "ontologies" / "instances" / "infraflex_instances.ttl"

# Map common AAS Property idShorts to standard MSC Ontology DatatypeProperties
IDSHORT_TO_MSC_DATAPROP = {
    "SiteIdentifier": SC.id,
    "SensorIdentifier": SC.id,
    "CharacteristicIdentifier": SC.id,
    "ProductIdentifier": SC.id,
    "ProcessIdentifier": SC.id,
    "ConfigurationIdentifier": SC.id,
    "RouteIdentifier": SC.id,
    "ResourceIdentifier": SC.id,
    "SerialNumber": SC.id,
    "SiteName": SC.name,
    "SensorName": SC.name,
    "ProductName": SC.name,
    "ProcessName": SC.name,
    "ConfigurationName": SC.name,
    "RouteName": SC.name,
    "ResourceName": SC.name,
    "ManufacturerProductDesignation": SC.name,
    "ResourceType": SC.resourceType,
    "ProcessType": SC.processType,
    "SensorType": SC.sensorType,
    "ManufacturerName": SC.manufacturer,
    "LocationAddress": SC.locationAddress,
    "LocationAspectDesignation": SC.locationAspectDesignation,
    "ProductAspectDesignation": SC.productAspectDesignation,
    "CharacteristicValue": SC.characteristicValue,
    "DataType": SC.dataType,
    "Unit": SC.unit,
    "UnitOfMeasure": SC.unit,
    "AvailableQuantity": SC.availableQuantity,
    "SamplingRate": SC.samplingRate,
    "RouteStatus": SC.routeStatus,
    "TransportationMode": SC.transportationMode,
    "TransmissionUnit": SC.transmissionUnit,
    "TransmissionCost": SC.transmissionCost,
    # LiveData operational telemetry fields
    "OperationalState": SC.operationalState,
    "CurrentSkill": SC.currentSkill,
    "TaskState": SC.taskState,
    "TaskDuration": SC.taskDuration,
    "GripperClose": SC.gripperClose,
    "ToolAttach": SC.toolAttach,
    "PowerToolEnable": SC.powerToolEnable,
    "AirErrorCode": SC.airErrorCode,
    "AirErrorMsg": SC.airErrorMsg,
    "ErrorCode": SC.errorCode,
    "ErrorMsg": SC.errorMsg,
    "Position": SC.position,
    "ServiceMode": SC.serviceMode,
    "ControlStop": SC.controlStop,
    "EmergencyStop": SC.emergencyStop,
    "LoweringEnable": SC.loweringEnable,
    "SlotEmpty": SC.slotEmpty,
    "SlotPositions": SC.slotPositions,
    "BatteryLevel": SC.batteryLevel,
    "CurrentLocation": SC.currentLocation,
    "PositionX": SC.positionX,
    "PositionY": SC.positionY,
    "Orientation": SC.orientation,
    "IOStatus": SC.ioStatus,
}


def _safe_local_name(identifier: str) -> str:
    """Convert an AAS shell ID or URN into a clean URI local name."""
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


def shell_id_to_uri(shell_id: str) -> URIRef:
    """Map an AAS shell ID to an MSC instance URIRef."""
    return INST[_safe_local_name(shell_id)]


def _semantic_id(element: dict) -> str:
    try:
        keys = element.get("semanticId", {}).get("keys", [])
        return keys[0].get("value", "") if keys else ""
    except Exception:
        return ""


def _lang_text(ml_values: list | Any) -> str:
    if not isinstance(ml_values, list):
        return ""
    for item in ml_values:
        if isinstance(item, dict) and item.get("language") == "en" and item.get("text"):
            return str(item["text"]).strip()
    for item in ml_values:
        if isinstance(item, dict) and item.get("text"):
            return str(item["text"]).strip()
    return ""


def _to_literal(val: Any, value_type: str = "") -> Literal | None:
    if val is None:
        return None
    s = str(val).strip()
    if s == "":
        return None
    vt = (value_type or "").lower()
    if isinstance(val, bool) or vt in ("xs:boolean", "boolean"):
        return Literal(s.lower() in ("true", "1", "yes"), datatype=XSD.boolean)
    if isinstance(val, int) or vt in ("xs:int", "xs:integer", "xs:long", "int", "integer"):
        try:
            return Literal(int(float(s)), datatype=XSD.integer)
        except ValueError:
            pass
    if isinstance(val, float) or vt in ("xs:double", "xs:float", "xs:decimal", "double", "float", "decimal"):
        try:
            return Literal(float(s), datatype=XSD.decimal)
        except ValueError:
            pass
    return Literal(s)


def _camel_prop(id_short: str) -> URIRef:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "", id_short or "property")
    if not cleaned:
        cleaned = "property"
    cleaned = cleaned[0].lower() + cleaned[1:]
    return SC[cleaned]


def _resolve_msc_class(sem_uri: str, shell_id: str) -> URIRef:
    """Determine the MSC Ontology class for an AAS shell."""
    if sem_uri.startswith(MSC_URI):
        local = sem_uri[len(MSC_URI):]
        if local:
            return SC[local]
    sid = shell_id.lower()
    if ":site:" in sid or ":cell:" in sid:
        return SC.Site
    if ":device:" in sid:
        return SC.Device
    if ":sensor:" in sid:
        return SC.Sensor
    if ":characteristic:" in sid:
        return SC.Characteristic
    if ":product:" in sid:
        return SC.Product
    if ":processconfiguration:" in sid:
        return SC.ProcessConfiguration
    if ":process:" in sid:
        return SC.Process
    if ":logisticroute:" in sid:
        return SC.LogisticRoute
    return SC.EquipmentResource


class MSCOntologyExporter:
    """Builds an MSC-Ontology-compliant RDF graph directly from the AAS/ files and live BaSyx state."""

    def __init__(
        self,
        aas_root: Path,
        msc_ontology_path: Path = DEFAULT_MSC_ONTOLOGY,
        next_steps_path: Path = DEFAULT_NEXT_STEPS,
        basyx_url: str = "",
    ):
        self.aas_root = aas_root
        self.msc_ontology_path = msc_ontology_path
        self.next_steps_path = next_steps_path
        self.basyx_url = basyx_url.rstrip("/")

    def build_graph(self, include_schema: bool = True, fetch_live_basyx: bool = True) -> Graph:
        g = Graph()
        g.bind("sc", SC)
        g.bind("inst", INST)
        g.bind("dcterms", DCTERMS)
        g.bind("owl", OWL)
        g.bind("rdf", RDF)
        g.bind("rdfs", RDFS)
        g.bind("xsd", XSD)

        if include_schema:
            if self.msc_ontology_path.is_file():
                g.parse(self.msc_ontology_path, format="turtle")
                log.info("Loaded MSC Ontology schema: %s (%d triples)", self.msc_ontology_path.name, len(g))
            if self.next_steps_path.is_file():
                g.parse(self.next_steps_path, format="turtle")

        subdirs = [
            "cells",
            "executors",
            "arms",
            "tools",
            "addons",
            "sensors",
            "characteristics",
            "products",
            "processes",
            "routes",
        ]

        file_count = 0
        for sd in subdirs:
            folder = self.aas_root / sd
            if not folder.is_dir():
                continue
            for json_path in sorted(folder.glob("*.json")):
                self._ingest_aas_json(g, json_path)
                file_count += 1

        log.info("Processed %d AAS JSON files into MSC Ontology graph (%d total triples).", file_count, len(g))

        if fetch_live_basyx and self.basyx_url:
            self._overlay_live_basyx(g)

        return g

    def _ingest_aas_json(self, g: Graph, json_path: Path):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Skipping %s: %s", json_path, exc)
            return

        sm_by_id = {sm.get("id"): sm for sm in data.get("submodels", []) if sm.get("id")}

        for shell in data.get("assetAdministrationShells", []):
            shell_id = shell.get("id", "")
            if not shell_id:
                continue
            id_short = shell.get("idShort", shell_id)
            sem_uri = _semantic_id(shell)
            msc_class = _resolve_msc_class(sem_uri, shell_id)
            subj = shell_id_to_uri(shell_id)

            g.add((subj, RDF.type, OWL.NamedIndividual))
            g.add((subj, RDF.type, msc_class))
            g.add((subj, SC.id, Literal(shell_id)))
            g.add((subj, SC.name, Literal(id_short)))
            g.add((subj, RDFS.label, Literal(id_short)))

            desc_txt = _lang_text(shell.get("description", []))
            if desc_txt:
                g.add((subj, RDFS.comment, Literal(desc_txt, lang="en")))

            for sm_ref in shell.get("submodels", []):
                sm_id = sm_ref.get("keys", [{}])[0].get("value", "")
                sm = sm_by_id.get(sm_id)
                if sm:
                    self._ingest_submodel(g, subj, shell_id, sm)

    def _ingest_submodel(self, g: Graph, subj: URIRef, shell_id: str, sm: dict):
        elements = sm.get("submodelElements", [])
        self._walk_elements(g, subj, shell_id, elements, parent_sem="", parent_idshort=sm.get("idShort", ""))

    def _walk_elements(
        self,
        g: Graph,
        subj: URIRef,
        shell_id: str,
        elements: list[dict],
        parent_sem: str = "",
        parent_idshort: str = "",
    ):
        for el in elements:
            if not isinstance(el, dict):
                continue
            mtype = el.get("modelType", "")
            id_short = el.get("idShort", "")
            sem = _semantic_id(el) or parent_sem

            if mtype == "Property":
                lit = _to_literal(el.get("value"), el.get("valueType", ""))
                if lit is None:
                    continue
                pred = IDSHORT_TO_MSC_DATAPROP.get(id_short)
                if pred is None:
                    if sem.startswith(MSC_URI):
                        pred = SC[sem[len(MSC_URI):]]
                    else:
                        pred = _camel_prop(id_short)
                g.add((subj, pred, lit))

            elif mtype == "MultiLanguageProperty":
                txt = _lang_text(el.get("value", []))
                if not txt:
                    continue
                pred = IDSHORT_TO_MSC_DATAPROP.get(id_short, _camel_prop(id_short))
                g.add((subj, pred, Literal(txt, lang="en")))

            elif mtype == "ReferenceElement":
                keys = el.get("value", {}).get("keys", [])
                if not keys:
                    continue
                target_id = keys[0].get("value", "")
                if not target_id:
                    continue
                target_uri = shell_id_to_uri(target_id)
                self._add_reference_triple(g, subj, target_uri, sem, id_short, parent_idshort)

            elif mtype in ("SubmodelElementCollection", "SubmodelElementList"):
                child_vals = el.get("value", [])
                if isinstance(child_vals, list):
                    # Handle Step_XX collections inside Process / ProcessConfiguration
                    if id_short.startswith("Step_") and (":process:" in shell_id or ":processconfiguration:" in shell_id):
                        step_uri = INST[f"{_safe_local_name(shell_id)}_{_safe_local_name(id_short)}"]
                        g.add((step_uri, RDF.type, SC.ProcessConfiguration))
                        g.add((step_uri, RDFS.label, Literal(id_short)))
                        g.add((subj, SC.hasStep, step_uri))
                        self._walk_elements(g, step_uri, shell_id, child_vals, parent_sem=sem, parent_idshort=id_short)
                    else:
                        self._walk_elements(g, subj, shell_id, child_vals, parent_sem=sem, parent_idshort=id_short)

    def _add_reference_triple(
        self,
        g: Graph,
        subj: URIRef,
        target_uri: URIRef,
        sem: str,
        id_short: str,
        parent_idshort: str,
    ):
        """Emit MSC Ontology object property triples (and their formal inverses)."""
        if sem.startswith(MSC_URI):
            local = sem[len(MSC_URI):]
            if local == "hasDevice_inverse":
                g.add((target_uri, SC.hasDevice, subj))
                return
            if local == "hasCharacteristic_inverse":
                g.add((target_uri, SC.hasCharacteristic, subj))
                return
            if local == "observes":
                g.add((subj, SC.observes, target_uri))
                g.add((target_uri, SC.isObservedBy, subj))
                return
            if local == "isObservedBy":
                g.add((subj, SC.isObservedBy, target_uri))
                g.add((target_uri, SC.observes, subj))
                return
            if local == "isPerformedAt":
                g.add((subj, SC.isPerformedAt, target_uri))
                g.add((target_uri, SC.performs, subj))
                return
            if local == "performs":
                g.add((subj, SC.performs, target_uri))
                g.add((target_uri, SC.isPerformedAt, subj))
                return
            g.add((subj, SC[local], target_uri))
            return

        # Fallback by collection/element idShort when semanticId is omitted
        combined = f"{parent_idshort}/{id_short}".lower()
        if "device" in combined or "robotarm" in combined:
            g.add((subj, SC.hasDevice, target_uri))
        elif "characteristic" in combined:
            g.add((subj, SC.hasCharacteristic, target_uri))
        elif "observ" in combined:
            g.add((subj, SC.observes, target_uri))
        elif "component" in combined:
            g.add((subj, SC.hasComponent, target_uri))
        elif "process" in combined:
            g.add((subj, SC.requiresProcess, target_uri))
        elif "start" in combined or "origin" in combined:
            g.add((subj, SC.hasStartingPoint, target_uri))
        elif "end" in combined or "destination" in combined:
            g.add((subj, SC.hasEndingPoint, target_uri))
        elif "cell" in combined or "site" in combined:
            g.add((subj, SC.isPerformedAt, target_uri))
        elif "resource" in combined or "tool" in combined or "addon" in combined:
            g.add((subj, SC.usesResource, target_uri))

    def _overlay_live_basyx(self, g: Graph):
        """Fetch live submodels from BaSyx (:8081) if reachable and overlay latest property values."""
        try:
            resp = requests.get(f"{self.basyx_url}/shells", timeout=3)
            if resp.status_code != 200:
                return
        except Exception:
            return

        log.info("Overlaying live state from BaSyx at %s ...", self.basyx_url)
        try:
            payload = resp.json()
            shells = payload.get("result", []) if isinstance(payload, dict) else payload
            for sh in shells:
                sh_id = sh.get("id", "")
                if not sh_id:
                    continue
                subj = shell_id_to_uri(sh_id)
                for sm_ref in sh.get("submodels", []):
                    sm_id = sm_ref.get("keys", [{}])[0].get("value", "")
                    if not sm_id:
                        continue
                    b64 = base64.urlsafe_b64encode(sm_id.encode()).decode().rstrip("=")
                    sm_resp = requests.get(f"{self.basyx_url}/submodels/{b64}", timeout=3)
                    if sm_resp.status_code == 200:
                        self._ingest_submodel(g, subj, sh_id, sm_resp.json())
        except Exception as exc:
            log.debug("Live BaSyx overlay warning: %s", exc)


def main():
    ap = argparse.ArgumentParser(description="Export AAS models to UniMaaS MSC Ontology TTL")
    ap.add_argument(
        "--aas-root",
        default=str(REPO_ROOT / "AAS"),
        help="Path to AAS/ folder (default: ../../AAS)",
    )
    ap.add_argument(
        "--ontology",
        default=str(DEFAULT_MSC_ONTOLOGY),
        help="Path to MSC-ontology-1.2.0.ttl",
    )
    ap.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_TTL),
        help="Output Turtle file path (default: ../../ontologies/instances/infraflex_instances.ttl)",
    )
    ap.add_argument(
        "--basyx",
        default="http://localhost:8081",
        help="BaSyx AAS Repository URL for live overlay (default: http://localhost:8081)",
    )
    ap.add_argument(
        "--skip-basyx",
        action="store_true",
        help="Export purely from static AAS JSON files without querying live BaSyx",
    )
    args = ap.parse_args()

    exporter = MSCOntologyExporter(
        aas_root=Path(args.aas_root),
        msc_ontology_path=Path(args.ontology),
        basyx_url="" if args.skip_basyx else args.basyx,
    )
    g = exporter.build_graph(include_schema=True, fetch_live_basyx=not args.skip_basyx)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(out_path), format="turtle")
    log.info("Exported %d MSC Ontology triples to %s", len(g), out_path)


if __name__ == "__main__":
    main()
