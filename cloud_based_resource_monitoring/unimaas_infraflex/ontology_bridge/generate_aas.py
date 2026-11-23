#!/usr/bin/env python3
"""
Generate complete AAS JSON files conforming to T4.2 MSC Ontology templates:
  • AAS/cells/            — sc:Site templates with sc:hasDevice and sc:hasCharacteristic
  • AAS/tools/            — sc:EquipmentResource templates with ResourceSubmodel & TechnicalData
  • AAS/addons/           — sc:EquipmentResource templates with ResourceSubmodel & TechnicalData
  • AAS/executors/        — sc:Device templates with DeviceSubmodel & LiveData
  • AAS/sensors/          — sc:Sensor templates observing telemetry characteristics
  • AAS/characteristics/  — sc:Characteristic templates characterizing devices and sites
  • AAS/products/         — sc:Product templates with sc:hasComponent and sc:requiresProcess
  • AAS/routes/           — sc:LogisticRoute templates with sc:hasStartingPoint and sc:hasEndingPoint

Run:
  python generate_aas.py [--aas-root ../../AAS] [--config ../config/devices.json]
"""

import argparse
import json
import zipfile
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Ontology URIs (exact namespaces from resource-def.ttl and MSC Ontology)
# ---------------------------------------------------------------------------

INFRAFLEX = "https://w3id.org/fm/infraflex#"
MSC = "http://unimaas-project.eu/MSCOntology#"

MSC_EQUIPMENT_RESOURCE = f"{MSC}EquipmentResource"
MSC_RESOURCE           = f"{MSC}Resource"
MSC_DEVICE             = f"{MSC}Device"
MSC_SENSOR             = f"{MSC}Sensor"
MSC_CHARACTERISTIC     = f"{MSC}Characteristic"
MSC_SITE               = f"{MSC}Site"
MSC_PRODUCT            = f"{MSC}Product"
MSC_LOGISTIC_ROUTE     = f"{MSC}LogisticRoute"
MSC_PROCESS            = f"{MSC}Process"
MSC_PROCESS_CONFIG     = f"{MSC}ProcessConfiguration"

# Object properties
MSC_OBSERVES               = f"{MSC}observes"
MSC_IS_OBSERVED_BY          = f"{MSC}isObservedBy"
MSC_HAS_CHARACTERISTIC     = f"{MSC}hasCharacteristic"
MSC_HAS_CHARACTERISTIC_INV = f"{MSC}hasCharacteristic_inverse"
MSC_HAS_DEVICE             = f"{MSC}hasDevice"
MSC_HAS_DEVICE_INV         = f"{MSC}hasDevice_inverse"
MSC_HAS_COMPONENT          = f"{MSC}hasComponent"
MSC_REQUIRES_PROCESS       = f"{MSC}requiresProcess"
MSC_USES_RESOURCE          = f"{MSC}usesResource"
MSC_IS_PERFORMED_AT        = f"{MSC}isPerformedAt"
MSC_HAS_STARTING_POINT     = f"{MSC}hasStartingPoint"
MSC_HAS_ENDING_POINT       = f"{MSC}hasEndingPoint"

# Semantic IDs for standard IDTA submodel templates
SM_NAMEPLATE = "https://admin-shell.io/zvei/nameplate/2/0/Nameplate"
SM_TECHNICAL = "https://admin-shell.io/ZVEI/TechnicalData/Submodel/1/2"

# Infraflex class URIs used as globalAssetId for type templates
CLASS_FINGERED_GRIPPER  = f"{INFRAFLEX}FingeredGripper"
CLASS_SUCTION_GRIPPER   = f"{INFRAFLEX}SuctionGripper"
CLASS_SCREWDRIVER       = f"{INFRAFLEX}ScrewDriver"
CLASS_ADDON_TABLE       = f"{INFRAFLEX}AddOnTable"
CLASS_ADDON_LIFT        = f"{INFRAFLEX}AddOnLift"
CLASS_ADDON_CONVEYOR    = f"{INFRAFLEX}AddOnConveyor"
CLASS_CELL              = f"{INFRAFLEX}Cell"

ADDON_LOCATIONS = ["A", "B", "C", "D", "E", "F"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ref(sm_id: str) -> dict:
    return {"type": "ModelReference", "keys": [{"type": "Submodel", "value": sm_id}]}


def _ext_ref(uri: str) -> dict:
    return {"type": "ExternalReference", "keys": [{"type": "GlobalReference", "value": uri}]}


def _prop(id_short: str, value_type: str, value: str, semantic_uri: str = "", unit: str = "") -> dict:
    el = {
        "modelType": "Property",
        "idShort": id_short,
        "valueType": f"xs:{value_type}",
        "value": str(value),
    }
    if semantic_uri:
        el["semanticId"] = _ext_ref(semantic_uri)
    if unit:
        el["qualifiers"] = [{"modelType": "Qualifier", "type": "Unit", "valueType": "xs:string", "value": unit}]
    return el


def _mlprop(id_short: str, value_en: str) -> dict:
    return {
        "modelType": "MultiLanguageProperty",
        "idShort": id_short,
        "value": [{"language": "en", "text": value_en}],
    }


def _coll(id_short: str, description: str, elements: list, semantic_uri: str = "") -> dict:
    c = {
        "modelType": "SubmodelElementCollection",
        "idShort": id_short,
        "description": [{"language": "en", "text": description}],
        "value": elements,
    }
    if semantic_uri:
        c["semanticId"] = _ext_ref(semantic_uri)
    return c


def _ref_el(id_short: str, target_id: str, semantic_uri: str = "",
            target_type: str = "AssetAdministrationShell", description: str = "") -> dict:
    el = {
        "modelType": "ReferenceElement",
        "idShort": id_short,
        "value": {"type": "ModelReference", "keys": [{"type": target_type, "value": target_id}]},
    }
    if semantic_uri:
        el["semanticId"] = _ext_ref(semantic_uri)
    if description:
        el["description"] = [{"language": "en", "text": description}]
    return el


def _shell(shell_id: str, id_short: str, description: str, asset_kind: str,
           global_asset_id: str, submodel_ids: list[str],
           semantic_uri: str = "") -> dict:
    s = {
        "modelType": "AssetAdministrationShell",
        "id": shell_id,
        "idShort": id_short,
        "description": [{"language": "en", "text": description}],
        "assetInformation": {
            "assetKind": asset_kind,
            "globalAssetId": global_asset_id,
        },
        "submodels": [_ref(sm_id) for sm_id in submodel_ids],
    }
    if semantic_uri:
        s["semanticId"] = _ext_ref(semantic_uri)
    return s


def _bundle(shells: list, submodels: list) -> dict:
    return {"assetAdministrationShells": shells, "submodels": submodels, "conceptDescriptions": []}


# ---------------------------------------------------------------------------
# Characteristic AAS generator (T4.2 template: characteristic1_test_aas.json)
# ---------------------------------------------------------------------------

def characteristic_aas(slug: str, prop_name: str, ident: str, val: str,
                       data_type: str, unit: str, entity_id: str = "",
                       sensor_id: str = "") -> dict:
    shell_id = f"urn:unimaas:infraflex:characteristic:{slug}"
    sm_id = f"urn:aas:infraflex:characteristic:{slug}:sm:characteristicsubmodel"
    global_asset_id = f"urn:asset:infraflex:characteristic:{slug}"

    elements = [
        _prop(prop_name, data_type, val),
        _prop("CharacteristicIdentifier", "string", ident),
        _prop("CharacteristicValue", data_type, val),
        _prop("DataType", "string", data_type),
        _prop("Unit", "string", unit),
    ]

    if entity_id:
        elements.append(_coll(
            "CharacterizesEntity",
            "ConfigurableEntity (Device, Resource, Site) that has this Characteristic",
            [
                _ref_el("Entity_Reference", entity_id, semantic_uri=MSC_HAS_CHARACTERISTIC_INV,
                        description=f"Entity possessing this characteristic ({prop_name})")
            ],
            semantic_uri=MSC_HAS_CHARACTERISTIC_INV,
        ))

    if sensor_id:
        elements.append(_coll(
            "ObservingSensors",
            "Sensors that observe this Characteristic (sc:isObservedBy)",
            [
                _ref_el("Sensor_Reference", sensor_id, semantic_uri=MSC_IS_OBSERVED_BY,
                        description=f"Sensor observing {prop_name}")
            ],
            semantic_uri=MSC_IS_OBSERVED_BY,
        ))

    sm = {
        "modelType": "Submodel",
        "id": sm_id,
        "idShort": "CharacteristicSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_CHARACTERISTIC),
        "description": [{"language": "en", "text": "Submodel representing a Characteristic from the MSCOntology"}],
        "displayName": [{"language": "en", "text": f"{prop_name} Characteristic Submodel"}],
        "submodelElements": elements,
    }

    shell = _shell(
        shell_id, slug,
        f"Infraflex characteristic: {prop_name} ({ident})",
        "Instance", global_asset_id, [sm_id],
        MSC_CHARACTERISTIC
    )
    return _bundle([shell], [sm])


# ---------------------------------------------------------------------------
# Sensor AAS generator (T4.2 template: sensor1_test_aas.json)
# ---------------------------------------------------------------------------

def sensor_aas(slug: str, sensor_name: str, ident: str, sensor_type: str,
               mfr_name: str, sampling_rate: float, char_ids: list[str],
               mqtt_endpoint: str) -> dict:
    shell_id = f"urn:unimaas:infraflex:sensor:{slug}"
    sm_id = f"urn:aas:infraflex:sensor:{slug}:sm:sensorsubmodel"
    global_asset_id = f"urn:asset:infraflex:sensor:{slug}"

    obs_refs = [
        _ref_el(f"Observed_{i+1}", cid, semantic_uri=MSC_OBSERVES,
                description=f"Observed characteristic {cid.split(':')[-1]}")
        for i, cid in enumerate(char_ids)
    ]

    elements = [
        _prop("SensorName", "string", sensor_name),
        _prop("SensorIdentifier", "string", ident),
        _prop("SensorType", "string", sensor_type),
        _prop("ManufacturerName", "string", mfr_name),
        _prop("SamplingRate", "double", str(sampling_rate)),
        _coll("ObservedCharacteristics", "Characteristics observed by this Sensor (sc:observes)",
              obs_refs, semantic_uri=MSC_OBSERVES),
        _coll("InterfaceDescription", "Communication interface and endpoint configuration", [
            _prop("InterfaceType", "string", "MQTT"),
            _prop("EndpointAddress", "string", mqtt_endpoint),
        ]),
    ]

    sm = {
        "modelType": "Submodel",
        "id": sm_id,
        "idShort": "SensorSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_SENSOR),
        "description": [{"language": "en", "text": "Submodel representing a Sensor from the MSCOntology"}],
        "displayName": [{"language": "en", "text": f"{sensor_name} Submodel"}],
        "submodelElements": elements,
    }

    shell = _shell(
        shell_id, slug,
        f"Infraflex sensor: {sensor_name} monitoring {mqtt_endpoint}",
        "Instance", global_asset_id, [sm_id],
        MSC_SENSOR
    )
    return _bundle([shell], [sm])


# ---------------------------------------------------------------------------
# Product AAS generator (T4.2 template: product_test_aas.json)
# ---------------------------------------------------------------------------

def product_aas(slug: str, product_name: str, ident: str, aspect_desig: str,
                components: list[tuple[str, int]] = None,
                requires_process_id: str = "") -> dict:
    shell_id = f"urn:unimaas:infraflex:product:{slug}"
    sm_id = f"urn:aas:infraflex:product:{slug}:sm:productsubmodel"
    global_asset_id = f"urn:asset:infraflex:product:{slug}"

    elements = [
        _prop("ProductName", "string", product_name),
        _prop("ProductIdentifier", "string", ident),
        _prop("ProductAspectDesignation", "string", aspect_desig),
    ]

    if components:
        comp_colls = []
        for i, (comp_id, qty) in enumerate(components, 1):
            comp_colls.append(_coll(f"Component_{i}", f"Component part #{i}", [
                _ref_el("ComponentProduct", comp_id, semantic_uri=MSC_HAS_COMPONENT,
                        description=f"Part component {comp_id.split(':')[-1]}"),
                _prop("Quantity", "integer", str(qty)),
            ]))
        elements.append(_coll("Components", "Component products that make up this product (sc:hasComponent)",
                              comp_colls, semantic_uri=MSC_HAS_COMPONENT))

    if requires_process_id:
        elements.append(_ref_el("RequiresProcess", requires_process_id,
                                semantic_uri=MSC_REQUIRES_PROCESS,
                                description="Process that manufactures or assemblies this product"))

    sm = {
        "modelType": "Submodel",
        "id": sm_id,
        "idShort": "ProductSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_PRODUCT),
        "description": [{"language": "en", "text": "Submodel representing a Product from the MSCOntology"}],
        "displayName": [{"language": "en", "text": f"{product_name} Submodel"}],
        "submodelElements": elements,
    }

    shell = _shell(
        shell_id, slug,
        f"Infraflex product: {product_name}",
        "Instance" if components else "Type", global_asset_id, [sm_id],
        MSC_PRODUCT
    )
    return _bundle([shell], [sm])


# ---------------------------------------------------------------------------
# Logistic Route AAS generator (T4.2 template: logisticRoute_test_aas.json)
# ---------------------------------------------------------------------------

def logistic_route_aas(slug: str, route_name: str, ident: str,
                       start_site_id: str, end_site_id: str,
                       resource_id: str, distance_m: float) -> dict:
    shell_id = f"urn:unimaas:infraflex:logisticroute:{slug}"
    sm_id = f"urn:aas:infraflex:logisticroute:{slug}:sm:logisticroutesubmodel"
    global_asset_id = f"urn:asset:infraflex:logisticroute:{slug}"

    elements = [
        _prop("RouteIdentifier", "string", ident),
        _prop("RouteName", "string", route_name),
        _prop("RouteStatus", "string", "Active"),
        _coll("StartingPoint", "Origin location of the route (sc:hasStartingPoint)", [
            _ref_el("Location_Start", start_site_id, semantic_uri=MSC_HAS_STARTING_POINT,
                    description=f"Start site {start_site_id.split(':')[-1]}")
        ], semantic_uri=MSC_HAS_STARTING_POINT),
        _coll("EndingPoint", "Destination location of the route (sc:hasEndingPoint)", [
            _ref_el("Location_End", end_site_id, semantic_uri=MSC_HAS_ENDING_POINT,
                    description=f"End site {end_site_id.split(':')[-1]}")
        ], semantic_uri=MSC_HAS_ENDING_POINT),
        _ref_el("UsesResource", resource_id, semantic_uri=MSC_USES_RESOURCE,
                description=f"Transportation resource {resource_id.split(':')[-1]}"),
        _coll("TransportCharacteristics", "Properties and characteristics of transportation", [
            _prop("TransportationMode", "string", "AMR"),
            _prop("Distance", "double", str(distance_m)),
            _prop("DistanceUnit", "string", "m"),
        ]),
    ]

    sm = {
        "modelType": "Submodel",
        "id": sm_id,
        "idShort": "LogisticRouteSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_LOGISTIC_ROUTE),
        "description": [{"language": "en", "text": "Submodel representing a Logistic Route from the MSCOntology"}],
        "displayName": [{"language": "en", "text": f"{route_name} Submodel"}],
        "submodelElements": elements,
    }

    shell = _shell(
        shell_id, slug,
        f"Infraflex logistic route: {route_name}",
        "Instance", global_asset_id, [sm_id],
        MSC_LOGISTIC_ROUTE
    )
    return _bundle([shell], [sm])


# ---------------------------------------------------------------------------
# Tool AAS generators (T4.2 template: resource_test_aas.json)
# ---------------------------------------------------------------------------

def fingered_gripper_aas(slug: str = "fingered_gripper") -> dict:
    base = f"urn:unimaas:infraflex:equipmentresource:{slug}"
    res_sm_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:resourcesubmodel"
    td_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:technicaldata"
    global_asset_id = f"urn:asset:infraflex:equipmentresource:{slug}"
    char_id = f"urn:unimaas:infraflex:characteristic:char_{slug}_payload"

    shell = _shell(
        base, slug,
        "Fingered (jaw) gripper — Infraflex equipment resource",
        "Type", global_asset_id, [res_sm_id, td_id],
        MSC_EQUIPMENT_RESOURCE
    )
    res_sm = {
        "modelType": "Submodel",
        "id": res_sm_id,
        "idShort": "ResourceSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_EQUIPMENT_RESOURCE),
        "submodelElements": [
            _prop("ResourceName", "string", "Fingered Gripper"),
            _prop("ResourceIdentifier", "string", "RES_TOOL_FINGERED_GRIPPER"),
            _prop("ResourceType", "string", "Equipment"),
            _prop("AvailableQuantity", "double", "1.0"),
            _prop("UnitOfMeasure", "string", "unit"),
            _coll("Characteristics", "Characteristics of this Resource (sc:hasCharacteristic)", [
                _ref_el("Characteristic_Payload", char_id, semantic_uri=MSC_HAS_CHARACTERISTIC)
            ], semantic_uri=MSC_HAS_CHARACTERISTIC),
        ],
    }
    td = {
        "modelType": "Submodel",
        "id": td_id,
        "idShort": "technicaldata",
        "kind": "Template",
        "semanticId": _ext_ref(CLASS_FINGERED_GRIPPER),
        "submodelElements": [
            _coll("GripperProperties", "Mechanical and force properties", [
                _prop("GripperPayload",   "decimal", "10.0", f"{INFRAFLEX}gripperPayload",  "kg"),
                _prop("GripperJawStroke", "decimal", "50.0", f"{INFRAFLEX}gripperJawStroke","mm"),
                _prop("GripperForceMin",  "decimal", "20.0", f"{INFRAFLEX}gripperForceMin", "N"),
                _prop("GripperForceMax",  "decimal", "180.0",f"{INFRAFLEX}gripperForceMax", "N"),
            ]),
        ],
    }
    return _bundle([shell], [res_sm, td])


def suction_gripper_aas(slug: str = "suction_gripper") -> dict:
    base = f"urn:unimaas:infraflex:equipmentresource:{slug}"
    res_sm_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:resourcesubmodel"
    td_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:technicaldata"
    global_asset_id = f"urn:asset:infraflex:equipmentresource:{slug}"
    char_id = f"urn:unimaas:infraflex:characteristic:char_{slug}_payload"

    shell = _shell(
        base, slug,
        "Vacuum suction gripper — Infraflex equipment resource",
        "Type", global_asset_id, [res_sm_id, td_id],
        MSC_EQUIPMENT_RESOURCE
    )
    res_sm = {
        "modelType": "Submodel",
        "id": res_sm_id,
        "idShort": "ResourceSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_EQUIPMENT_RESOURCE),
        "submodelElements": [
            _prop("ResourceName", "string", "Suction Gripper"),
            _prop("ResourceIdentifier", "string", "RES_TOOL_SUCTION_GRIPPER"),
            _prop("ResourceType", "string", "Equipment"),
            _prop("AvailableQuantity", "double", "1.0"),
            _prop("UnitOfMeasure", "string", "unit"),
            _coll("Characteristics", "Characteristics of this Resource (sc:hasCharacteristic)", [
                _ref_el("Characteristic_Payload", char_id, semantic_uri=MSC_HAS_CHARACTERISTIC)
            ], semantic_uri=MSC_HAS_CHARACTERISTIC),
        ],
    }
    td = {
        "modelType": "Submodel",
        "id": td_id,
        "idShort": "technicaldata",
        "kind": "Template",
        "semanticId": _ext_ref(CLASS_SUCTION_GRIPPER),
        "submodelElements": [
            _coll("GripperProperties", "Vacuum and cup properties", [
                _prop("GripperPayload",       "decimal", "5.0",  f"{INFRAFLEX}gripperPayload",      "kg"),
                _prop("GripperSuctionCupSize","decimal", "30.0", f"{INFRAFLEX}gripperSuctionCupSize","mm"),
                _prop("GripperSealTime",      "decimal", "0.2",  f"{INFRAFLEX}gripperSeal",         "s"),
                _prop("GripperReleaseTime",   "decimal", "0.1",  f"{INFRAFLEX}gripperReleaseTime",  "s"),
            ]),
        ],
    }
    return _bundle([shell], [res_sm, td])


def screwdriver_aas(slug: str = "screwdriver") -> dict:
    base = f"urn:unimaas:infraflex:equipmentresource:{slug}"
    res_sm_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:resourcesubmodel"
    td_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:technicaldata"
    global_asset_id = f"urn:asset:infraflex:equipmentresource:{slug}"
    char_id = f"urn:unimaas:infraflex:characteristic:char_{slug}_torque"

    shell = _shell(
        base, slug,
        "Robotic screwdriver — Infraflex equipment resource",
        "Type", global_asset_id, [res_sm_id, td_id],
        MSC_EQUIPMENT_RESOURCE
    )
    res_sm = {
        "modelType": "Submodel",
        "id": res_sm_id,
        "idShort": "ResourceSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_EQUIPMENT_RESOURCE),
        "submodelElements": [
            _prop("ResourceName", "string", "Screw Driver"),
            _prop("ResourceIdentifier", "string", "RES_TOOL_SCREWDRIVER"),
            _prop("ResourceType", "string", "Equipment"),
            _prop("AvailableQuantity", "double", "1.0"),
            _prop("UnitOfMeasure", "string", "unit"),
            _coll("Characteristics", "Characteristics of this Resource (sc:hasCharacteristic)", [
                _ref_el("Characteristic_Torque", char_id, semantic_uri=MSC_HAS_CHARACTERISTIC)
            ], semantic_uri=MSC_HAS_CHARACTERISTIC),
        ],
    }
    td = {
        "modelType": "Submodel",
        "id": td_id,
        "idShort": "technicaldata",
        "kind": "Template",
        "semanticId": _ext_ref(CLASS_SCREWDRIVER),
        "submodelElements": [
            _coll("ScrewDriverProperties", "Torque and screw dimensions", [
                _prop("ScrewDriverMaxTorque",     "decimal", "8.5",  f"{INFRAFLEX}screwDriverMaxTorque",   "Nm"),
                _prop("ScrewDriverScrewMaxLength","decimal", "25.0", f"{INFRAFLEX}screwDriverScrewMaxLength","mm"),
            ]),
        ],
    }
    return _bundle([shell], [res_sm, td])


# ---------------------------------------------------------------------------
# Add-on AAS generators (T4.2 template: resource_test_aas.json)
# ---------------------------------------------------------------------------

def addon_table_aas(slug: str = "addon_table") -> dict:
    base = f"urn:unimaas:infraflex:equipmentresource:{slug}"
    res_sm_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:resourcesubmodel"
    td_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:technicaldata"
    global_asset_id = f"urn:asset:infraflex:equipmentresource:{slug}"

    shell = _shell(
        base, slug,
        "Infraflex cell add-on: work table",
        "Type", global_asset_id, [res_sm_id, td_id],
        MSC_EQUIPMENT_RESOURCE
    )
    res_sm = {
        "modelType": "Submodel",
        "id": res_sm_id,
        "idShort": "ResourceSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_EQUIPMENT_RESOURCE),
        "submodelElements": [
            _prop("ResourceName", "string", "Add-On Table"),
            _prop("ResourceIdentifier", "string", "RES_ADDON_TABLE"),
            _prop("ResourceType", "string", "Equipment"),
            _prop("AvailableQuantity", "double", "1.0"),
            _prop("UnitOfMeasure", "string", "unit"),
        ],
    }
    td = {
        "modelType": "Submodel",
        "id": td_id,
        "idShort": "technicaldata",
        "kind": "Template",
        "semanticId": _ext_ref(CLASS_ADDON_TABLE),
        "submodelElements": [
            _coll("AddOnProperties", "Table dimensions", [
                _prop("TableWidth",  "decimal", "600.0", f"{INFRAFLEX}addOnTableSizeWidth",  "mm"),
                _prop("TableLength", "decimal", "800.0", f"{INFRAFLEX}addOnTableSizeLength", "mm"),
            ]),
        ],
    }
    return _bundle([shell], [res_sm, td])


def addon_lift_aas(slug: str = "addon_lift") -> dict:
    base = f"urn:unimaas:infraflex:equipmentresource:{slug}"
    res_sm_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:resourcesubmodel"
    td_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:technicaldata"
    global_asset_id = f"urn:asset:infraflex:equipmentresource:{slug}"

    shell = _shell(
        base, slug,
        "Infraflex cell add-on: lift table",
        "Type", global_asset_id, [res_sm_id, td_id],
        MSC_EQUIPMENT_RESOURCE
    )
    res_sm = {
        "modelType": "Submodel",
        "id": res_sm_id,
        "idShort": "ResourceSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_EQUIPMENT_RESOURCE),
        "submodelElements": [
            _prop("ResourceName", "string", "Add-On Lift"),
            _prop("ResourceIdentifier", "string", "RES_ADDON_LIFT"),
            _prop("ResourceType", "string", "Equipment"),
            _prop("AvailableQuantity", "double", "1.0"),
            _prop("UnitOfMeasure", "string", "unit"),
        ],
    }
    td = {
        "modelType": "Submodel",
        "id": td_id,
        "idShort": "technicaldata",
        "kind": "Template",
        "semanticId": _ext_ref(CLASS_ADDON_LIFT),
        "submodelElements": [
            _coll("AddOnProperties", "Table and lift dimensions", [
                _prop("TableWidth",       "decimal", "600.0", f"{INFRAFLEX}addOnTableSizeWidth",  "mm"),
                _prop("TableLength",      "decimal", "800.0", f"{INFRAFLEX}addOnTableSizeLength", "mm"),
                _prop("LiftStrokeHeight", "decimal", "300.0", f"{INFRAFLEX}addOnLiftStrokeHeight","mm"),
            ]),
        ],
    }
    return _bundle([shell], [res_sm, td])


def addon_conveyor_aas(slug: str = "addon_conveyor") -> dict:
    base = f"urn:unimaas:infraflex:equipmentresource:{slug}"
    res_sm_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:resourcesubmodel"
    td_id = f"urn:aas:infraflex:equipmentresource:{slug}:sm:technicaldata"
    global_asset_id = f"urn:asset:infraflex:equipmentresource:{slug}"

    shell = _shell(
        base, slug,
        "Infraflex cell add-on: conveyor",
        "Type", global_asset_id, [res_sm_id, td_id],
        MSC_EQUIPMENT_RESOURCE
    )
    res_sm = {
        "modelType": "Submodel",
        "id": res_sm_id,
        "idShort": "ResourceSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_EQUIPMENT_RESOURCE),
        "submodelElements": [
            _prop("ResourceName", "string", "Add-On Conveyor"),
            _prop("ResourceIdentifier", "string", "RES_ADDON_CONVEYOR"),
            _prop("ResourceType", "string", "Equipment"),
            _prop("AvailableQuantity", "double", "1.0"),
            _prop("UnitOfMeasure", "string", "unit"),
        ],
    }
    td = {
        "modelType": "Submodel",
        "id": td_id,
        "idShort": "technicaldata",
        "kind": "Template",
        "semanticId": _ext_ref(CLASS_ADDON_CONVEYOR),
        "submodelElements": [
            _coll("AddOnProperties", "Conveyor dimensions", [
                _prop("ConveyorWidth",  "decimal", "400.0",  f"{INFRAFLEX}addOnConveyorWidth", "mm"),
                _prop("ConveyorStroke", "decimal", "1200.0", f"{INFRAFLEX}addOnConveyorStroke","mm"),
            ]),
        ],
    }
    return _bundle([shell], [res_sm, td])


# ---------------------------------------------------------------------------
# Cell AAS generator (T4.2 template: site_test_aas.json)
# ---------------------------------------------------------------------------

def _arm_id_from_aasx(aasx_path: Path) -> str | None:
    try:
        with zipfile.ZipFile(aasx_path) as z:
            jf = next((n for n in z.namelist() if n.endswith(".json")), None)
            if not jf:
                return None
            d = json.loads(z.read(jf))
            return d.get("assetAdministrationShells", [{}])[0].get("id")
    except Exception:
        return None


def cell_aas(cell_name: str, executor_types: list[str],
             arm_aas_id: str | None, arms_dir: Path) -> dict:
    """
    Build a Cell AAS adhering to T4.2 sc:Site template:
    • Shell semanticId: http://unimaas-project.eu/MSCOntology#Site
    • SiteSubmodel with SiteName, SiteIdentifier, LocationAddress, LocationAspectDesignation,
      Devices collection (sc:hasDevice to robot arm and executor devices),
      Characteristics collection (sc:hasCharacteristic to available capacity)
    • Configuration submodel preserving peripheral slots A-F and tool slots
    """
    cell_slug = cell_name.lower().replace(" ", "_")
    base = f"urn:unimaas:infraflex:site:{cell_slug}"
    site_sm_id = f"urn:aas:infraflex:site:{cell_slug}:sm:sitesubmodel"
    conf_id    = f"urn:aas:infraflex:site:{cell_slug}:sm:configuration"
    global_asset_id = f"urn:asset:infraflex:site:{cell_slug}"
    capacity_char_id = f"urn:unimaas:infraflex:characteristic:char_{cell_slug}_available_capacity"

    if arm_aas_id is None:
        arm_aas_id = f"urn:unimaas:infraflex:equipmentresource:dossan_h2017"

    # Collect all devices at this site
    devices_in_cell = []
    if arm_aas_id:
        devices_in_cell.append(arm_aas_id)
    for exe_type in executor_types:
        exe_slug = f"{cell_slug}_{exe_type.lower()}"
        devices_in_cell.append(f"urn:unimaas:infraflex:device:{exe_slug}")

    device_refs = [
        _ref_el(f"Device_{i+1}", dev_id, semantic_uri=MSC_HAS_DEVICE,
                description=f"Device at {cell_name} ({dev_id.split(':')[-1]})")
        for i, dev_id in enumerate(devices_in_cell)
    ]

    site_sm = {
        "modelType": "Submodel",
        "id": site_sm_id,
        "idShort": "SiteSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_SITE),
        "description": [{"language": "en", "text": "Submodel representing a Manufacturing Site from the MSCOntology"}],
        "displayName": [{"language": "en", "text": f"{cell_name} Site Submodel"}],
        "submodelElements": [
            _prop("SiteName", "string", cell_name),
            _prop("SiteIdentifier", "string", cell_slug.upper()),
            _prop("LocationAddress", "string", f"UniMaaS Smart Factory, {cell_name}"),
            _prop("LocationAspectDesignation", "string", f"+{cell_name}"),
            _coll("Devices", "Devices at this Site (sc:hasDevice)", device_refs, semantic_uri=MSC_HAS_DEVICE),
            _coll("Characteristics", "Characteristics of this Site (sc:hasCharacteristic)", [
                _ref_el("Characteristic_AvailableCapacity", capacity_char_id, semantic_uri=MSC_HAS_CHARACTERISTIC,
                        description=f"Available processing capacity of {cell_name}")
            ], semantic_uri=MSC_HAS_CHARACTERISTIC),
        ],
    }

    # Configuration submodel
    robot_arm_elements = [
        _ref_el("RobotArm", arm_aas_id, description=f"Robot arm installed in {cell_name}")
    ]
    executor_elements = []
    for exe_type in executor_types:
        exe_slug = f"{cell_slug}_{exe_type.lower()}"
        exe_shell_id = f"urn:unimaas:infraflex:device:{exe_slug}"
        executor_elements.append(
            _coll(exe_type, f"{exe_type} executor in {cell_name}", [
                _prop("DeviceType", "string", exe_type, "https://admin-shell.io/idta/asset/type/1/0"),
                _prop("MqttPath", "string", f"Infraflex/Executors/{cell_name}/{exe_type}",
                      "https://admin-shell.io/idta/communication/endpoint/1/0"),
                _ref_el("DeviceShell", exe_shell_id, description=f"AAS shell for {exe_type} in {cell_name}"),
                _prop("OperationalState", "string", "", f"{INFRAFLEX}operationalState"),
                _prop("CurrentSkill", "string", "", f"{INFRAFLEX}currentSkill"),
            ])
        )

    addon_slot_elements = [
        _coll(f"Slot{loc}", f"Add-on slot {loc}", [
            _prop("Location", "string", loc, f"{INFRAFLEX}cellAddOnLocation"),
            _prop("InstalledAddOnShellId", "string", "", f"{INFRAFLEX}cellAddOnAddOn"),
        ])
        for loc in ADDON_LOCATIONS
    ]
    tool_slot_elements = [
        _prop(f"Tool{i+1}ShellId", "string", "", f"{INFRAFLEX}cellTool")
        for i in range(2)
    ]

    conf = {
        "modelType": "Submodel",
        "id": conf_id,
        "idShort": "configuration",
        "kind": "Instance",
        "semanticId": _ext_ref(CLASS_CELL),
        "submodelElements": [
            _coll("RobotArm",     "Robot arm installed in this cell", robot_arm_elements),
            _coll("Executors",    "Peripheral executor devices",      executor_elements),
            _coll("CellAddOnSubs","Add-on peripheral slots (A–F)",   addon_slot_elements),
            _coll("CellTools",    "Tools available in this cell",     tool_slot_elements),
        ],
    }

    shell = _shell(
        base, cell_slug,
        f"Infraflex production site {cell_name}",
        "Instance", global_asset_id, [site_sm_id, conf_id],
        MSC_SITE
    )
    return _bundle([shell], [site_sm, conf])


# ---------------------------------------------------------------------------
# Executor Device AAS generator (T4.2 template: device_test_aas.json)
# ---------------------------------------------------------------------------

LIVEDATA_PROPERTIES: dict[str, list[tuple[str, str, str]]] = {
    "PowerAir": [
        ("OperationalState",  "string",  f"{INFRAFLEX}operationalState"),
        ("GripperClose",      "boolean", f"{INFRAFLEX}gripperClose"),
        ("ToolAttach",        "boolean", f"{INFRAFLEX}toolAttach"),
        ("AirErrorCode",      "int",     ""),
        ("AirErrorMsg",       "string",  ""),
        ("PowerToolEnable",   "boolean", f"{INFRAFLEX}toolEnable"),
        ("CurrentSkill",      "string",  f"{INFRAFLEX}currentSkill"),
        ("TaskState",         "string",  f"{INFRAFLEX}taskState"),
        ("TaskDuration",      "decimal", ""),
        ("LastUpdate",        "string",  ""),
    ],
    "Lift": [
        ("OperationalState",  "string",  f"{INFRAFLEX}operationalState"),
        ("Position",          "string",  f"{INFRAFLEX}liftPosition"),
        ("ServiceMode",       "boolean", ""),
        ("ErrorCode",         "int",     ""),
        ("ErrorMsg",          "string",  ""),
        ("ControlStop",       "boolean", ""),
        ("EmergencyStop",     "boolean", f"{INFRAFLEX}emergencyStop"),
        ("LoweringEnable",    "boolean", ""),
        ("CurrentSkill",      "string",  f"{INFRAFLEX}currentSkill"),
        ("TaskState",         "string",  f"{INFRAFLEX}taskState"),
        ("LastUpdate",        "string",  ""),
    ],
    "Turntable": [
        ("OperationalState",  "string",  f"{INFRAFLEX}operationalState"),
        ("SlotEmpty",         "string",  f"{INFRAFLEX}slotEmpty"),
        ("SlotPositions",     "string",  f"{INFRAFLEX}slotPositions"),
        ("ErrorCode",         "int",     ""),
        ("ErrorMsg",          "string",  ""),
        ("CurrentSkill",      "string",  f"{INFRAFLEX}currentSkill"),
        ("TaskState",         "string",  f"{INFRAFLEX}taskState"),
        ("LastUpdate",        "string",  ""),
    ],
    "AMR": [
        ("OperationalState",  "string",  f"{INFRAFLEX}operationalState"),
        ("BatteryLevel",      "decimal", f"{INFRAFLEX}batteryLevel"),
        ("CurrentLocation",   "string",  f"{INFRAFLEX}currentLocation"),
        ("PositionX",         "decimal", ""),
        ("PositionY",         "decimal", ""),
        ("Orientation",       "decimal", ""),
        ("ErrorCode",         "int",     ""),
        ("ErrorMsg",          "string",  ""),
        ("CurrentSkill",      "string",  f"{INFRAFLEX}currentSkill"),
        ("TaskState",         "string",  f"{INFRAFLEX}taskState"),
        ("LastUpdate",        "string",  ""),
    ],
    "Warehouse": [
        ("OperationalState",  "string",  f"{INFRAFLEX}operationalState"),
        ("ErrorCode",         "int",     ""),
        ("ErrorMsg",          "string",  ""),
        ("EmergencyStop",     "boolean", f"{INFRAFLEX}emergencyStop"),
        ("IOStatus",          "boolean", ""),
        ("CurrentSkill",      "string",  f"{INFRAFLEX}currentSkill"),
        ("TaskState",         "string",  f"{INFRAFLEX}taskState"),
        ("LastUpdate",        "string",  ""),
    ],
    "Debonder": [
        ("OperationalState",  "string",  f"{INFRAFLEX}operationalState"),
        ("ErrorCode",         "int",     ""),
        ("ErrorMsg",          "string",  ""),
        ("CurrentSkill",      "string",  f"{INFRAFLEX}currentSkill"),
        ("TaskState",         "string",  f"{INFRAFLEX}taskState"),
        ("LastUpdate",        "string",  ""),
    ],
}

EXECUTOR_INFRAFLEX_CLASS = {
    "PowerAir":  f"{INFRAFLEX}PowerAirExecutor",
    "Lift":      f"{INFRAFLEX}LiftExecutor",
    "Turntable": f"{INFRAFLEX}TurntableExecutor",
    "AMR":       f"{INFRAFLEX}AMRExecutor",
    "Warehouse": f"{INFRAFLEX}WarehouseExecutor",
    "Debonder":  f"{INFRAFLEX}DebonderExecutor",
}


def executor_device_aas(shell_id: str, submodel_id: str, vo_id: str,
                        device_type: str, mqtt_path: str, site_id: str,
                        char_ids: list[str]) -> dict:
    """
    Build a Device AAS adhering to T4.2 sc:Device template:
    • Shell semanticId: http://unimaas-project.eu/MSCOntology#Device
    • Submodel 1: DeviceSubmodel with Nameplate, PartOfSite (sc:hasDevice_inverse),
      and Characteristics (sc:hasCharacteristic to each individual telemetry characteristic)
    • Submodel 2: LiveData with real-time properties for MQTT / VO manager integration
    """
    id_short = shell_id.split(":")[-1]
    global_asset_id = f"urn:asset:infraflex:device:{id_short}"
    dev_sm_id = f"urn:aas:infraflex:device:{id_short}:sm:devicesubmodel"

    char_refs = [
        _ref_el(f"Characteristic_{i+1}", cid, semantic_uri=MSC_HAS_CHARACTERISTIC,
                description=f"Observable characteristic {cid.split(':')[-1]}")
        for i, cid in enumerate(char_ids)
    ]

    dev_sm_elements = [
        _coll("Nameplate", "Identifying information about this Device", [
            _prop("ManufacturerName", "string", "Infraflex"),
            _prop("ManufacturerProductDesignation", "string", f"{device_type} Unit"),
            _prop("SerialNumber", "string", f"SN-INFRA-{id_short.upper()}"),
        ]),
        _ref_el("PartOfSite", site_id, semantic_uri=MSC_HAS_DEVICE_INV,
                description=f"Site hosting this Device ({site_id.split(':')[-1]})"),
        _coll("Characteristics", "Characteristics of this Device (sc:hasCharacteristic)",
              char_refs, semantic_uri=MSC_HAS_CHARACTERISTIC),
    ]

    dev_sm = {
        "modelType": "Submodel",
        "id": dev_sm_id,
        "idShort": "DeviceSubmodel",
        "kind": "Instance",
        "semanticId": _ext_ref(MSC_DEVICE),
        "description": [{"language": "en", "text": "Submodel representing a Device from the MSCOntology"}],
        "displayName": [{"language": "en", "text": f"{vo_id} Device Submodel"}],
        "submodelElements": dev_sm_elements,
    }

    # LiveData submodel (preserves existing MQTT / VO Manager contract)
    live_props = [
        _prop(name, vtype, "", sem_uri)
        for name, vtype, sem_uri in LIVEDATA_PROPERTIES.get(device_type, [])
    ]
    live_sm = {
        "modelType": "Submodel",
        "id": submodel_id,
        "idShort": "livedata",
        "kind": "Instance",
        "submodelElements": live_props,
    }

    class_uri = EXECUTOR_INFRAFLEX_CLASS.get(device_type, f"{INFRAFLEX}{device_type}Executor")
    shell = _shell(
        shell_id, id_short,
        f"Infraflex device: {mqtt_path} ({device_type})",
        "Instance", global_asset_id, [dev_sm_id, submodel_id],
        MSC_DEVICE,
    )
    shell["assetInformation"]["assetType"] = class_uri

    return _bundle([shell], [dev_sm, live_sm])


# ---------------------------------------------------------------------------
# Main generation orchestration
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate Infraflex AAS files conforming to T4.2 templates")
    parser.add_argument("--aas-root", default=str(Path(__file__).parent.parent.parent / "AAS"))
    parser.add_argument("--config",   default=str(Path(__file__).parent.parent / "config" / "devices.json"))
    args = parser.parse_args()

    aas_root = Path(args.aas_root)
    arms_dir            = aas_root / "arms"
    tools_dir           = aas_root / "tools"
    addons_dir          = aas_root / "addons"
    cells_dir           = aas_root / "cells"
    executors_dir       = aas_root / "executors"
    sensors_dir         = aas_root / "sensors"
    characteristics_dir = aas_root / "characteristics"
    products_dir        = aas_root / "products"
    routes_dir          = aas_root / "routes"

    for d in (tools_dir, addons_dir, cells_dir, executors_dir,
              sensors_dir, characteristics_dir, products_dir, routes_dir):
        d.mkdir(parents=True, exist_ok=True)

    with open(args.config) as f:
        cfg = json.load(f)

    # Robot arms mapping
    arm_ids: dict[str, str] = {}
    for arm_file in sorted(arms_dir.glob("*.json")):
        arm_ids[arm_file.stem] = f"urn:unimaas:infraflex:equipmentresource:{arm_file.stem}"
    CELL_ARM = {
        "Cell1": arm_ids.get("tx2_90xl", "urn:unimaas:infraflex:equipmentresource:tx2_90xl"),
        "Cell2": arm_ids.get("dossan_h2515", "urn:unimaas:infraflex:equipmentresource:dossan_h2515"),
        "Cell3": arm_ids.get("ur10e", "urn:unimaas:infraflex:equipmentresource:ur10e"),
        "Cell4": arm_ids.get("kr_10_1100_2", "urn:unimaas:infraflex:equipmentresource:kr_10_1100_2"),
        "Cell5": arm_ids.get("crx_20ia", "urn:unimaas:infraflex:equipmentresource:crx_20ia"),
        "Cell6": arm_ids.get("dossan_h2017", "urn:unimaas:infraflex:equipmentresource:dossan_h2017"),
    }

    # 1. ---- Characteristics for Tools ----
    tool_chars = [
        ("char_fingered_gripper_payload", "GripperPayload", "CHAR_TOOL_FG_PAYLOAD", "10.0", "double", "kg",
         "urn:unimaas:infraflex:equipmentresource:fingered_gripper"),
        ("char_suction_gripper_payload", "GripperPayload", "CHAR_TOOL_SG_PAYLOAD", "5.0", "double", "kg",
         "urn:unimaas:infraflex:equipmentresource:suction_gripper"),
        ("char_screwdriver_torque", "ScrewDriverMaxTorque", "CHAR_TOOL_SD_TORQUE", "8.5", "double", "Nm",
         "urn:unimaas:infraflex:equipmentresource:screwdriver"),
    ]
    for slug, prop, ident, val, dtype, unit, entity in tool_chars:
        data = characteristic_aas(slug, prop, ident, val, dtype, unit, entity_id=entity)
        (characteristics_dir / f"{slug}.json").write_text(json.dumps(data, indent=2))

    # 2. ---- Site Capacity Characteristics ----
    for i in range(1, 7):
        cslug = f"char_cell{i}_available_capacity"
        data = characteristic_aas(cslug, "AvailableCapacity", f"CHAR_CELL{i}_CAPACITY",
                                  "100.0", "double", "%",
                                  entity_id=f"urn:unimaas:infraflex:site:cell{i}")
        (characteristics_dir / f"{cslug}.json").write_text(json.dumps(data, indent=2))

    # 3. ---- Telemetry Characteristics & Sensors for each device ----
    char_count = 0
    sensor_count = 0

    # Specification of primary observable characteristics per device type
    DEV_CHAR_SPECS = {
        "PowerAir": [
            ("operational_state", "OperationalState", "string", "IDLE", ""),
            ("gripper_close", "GripperClose", "boolean", "false", ""),
            ("tool_attach", "ToolAttach", "boolean", "false", ""),
            ("air_error_code", "AirErrorCode", "integer", "0", ""),
            ("power_tool_enable", "PowerToolEnable", "boolean", "true", ""),
        ],
        "Lift": [
            ("operational_state", "OperationalState", "string", "IDLE", ""),
            ("position", "Position", "string", "DOWN", ""),
            ("emergency_stop", "EmergencyStop", "boolean", "false", ""),
            ("error_code", "ErrorCode", "integer", "0", ""),
        ],
        "Turntable": [
            ("operational_state", "OperationalState", "string", "IDLE", ""),
            ("slot_empty", "SlotEmpty", "string", "true", ""),
            ("slot_positions", "SlotPositions", "string", "0,90,180,270", "deg"),
            ("error_code", "ErrorCode", "integer", "0", ""),
        ],
        "AMR": [
            ("operational_state", "OperationalState", "string", "IDLE", ""),
            ("battery_level", "BatteryLevel", "double", "98.5", "%"),
            ("current_location", "CurrentLocation", "string", "Cell1", ""),
            ("position_x", "PositionX", "double", "1.25", "m"),
            ("position_y", "PositionY", "double", "3.40", "m"),
            ("error_code", "ErrorCode", "integer", "0", ""),
        ],
        "Warehouse": [
            ("operational_state", "OperationalState", "string", "IDLE", ""),
            ("emergency_stop", "EmergencyStop", "boolean", "false", ""),
            ("error_code", "ErrorCode", "integer", "0", ""),
        ],
        "Debonder": [
            ("operational_state", "OperationalState", "string", "IDLE", ""),
            ("error_code", "ErrorCode", "integer", "0", ""),
        ],
    }

    device_char_map: dict[str, list[str]] = defaultdict(list)

    for device in cfg["devices"]:
        dev_slug = device["aas_shell_id"].split(":")[-1]
        dev_type = device["type"]
        sensor_slug = f"{dev_slug}_sensor"
        sensor_id = f"urn:unimaas:infraflex:sensor:{sensor_slug}"

        specs = DEV_CHAR_SPECS.get(dev_type, [("operational_state", "OperationalState", "string", "IDLE", "")])
        for cname, pname, dtype, def_val, unit in specs:
            cslug = f"char_{dev_slug}_{cname}"
            cid = f"urn:unimaas:infraflex:characteristic:{cslug}"
            device_char_map[dev_slug].append(cid)

            cdata = characteristic_aas(
                slug=cslug,
                prop_name=pname,
                ident=f"CHAR_{dev_slug.upper()}_{cname.upper()}",
                val=def_val,
                data_type=dtype,
                unit=unit,
                entity_id=device["aas_shell_id"],
                sensor_id=sensor_id,
            )
            (characteristics_dir / f"{cslug}.json").write_text(json.dumps(cdata, indent=2))
            char_count += 1

        # Sensor AAS for this device
        sdata = sensor_aas(
            slug=sensor_slug,
            sensor_name=f"{device['vo_id']} Sensor",
            ident=f"SENS_{dev_slug.upper()}",
            sensor_type=f"{dev_type} Telemetry",
            mfr_name="Infraflex Telemetry Unit",
            sampling_rate=1.0,
            char_ids=device_char_map[dev_slug],
            mqtt_endpoint=f"Infraflex/Executors/{device['mqtt_path']}",
        )
        (sensors_dir / f"{sensor_slug}.json").write_text(json.dumps(sdata, indent=2))
        sensor_count += 1

    # 4. ---- Device AAS (executors) ----
    executor_count = 0
    for device in cfg["devices"]:
        dev_slug = device["aas_shell_id"].split(":")[-1]
        path_parts = device["mqtt_path"].split("/")
        site_id = "urn:unimaas:infraflex:site:cell1"
        if path_parts and path_parts[0].lower().startswith("cell"):
            site_id = f"urn:unimaas:infraflex:site:{path_parts[0].lower()}"

        data = executor_device_aas(
            shell_id=device["aas_shell_id"],
            submodel_id=device["aas_submodel_id"],
            vo_id=device["vo_id"],
            device_type=device["type"],
            mqtt_path=device["mqtt_path"],
            site_id=site_id,
            char_ids=device_char_map[dev_slug],
        )
        (executors_dir / f"{device['vo_id'].lower()}.json").write_text(json.dumps(data, indent=2))
        executor_count += 1

    # 5. ---- Sites (cells) ----
    cells: dict[str, list[str]] = defaultdict(list)
    for device in cfg["devices"]:
        path = device["mqtt_path"]
        parts = path.split("/")
        if len(parts) == 2 and parts[0].startswith("Cell"):
            cells[parts[0]].append(parts[1])
    for cell_name in CELL_ARM:
        cells.setdefault(cell_name, [])

    for cell_name, exe_types in sorted(cells.items()):
        arm_id = CELL_ARM.get(cell_name)
        data = cell_aas(cell_name, exe_types, arm_id, arms_dir)
        (cells_dir / f"{cell_name.lower()}.json").write_text(json.dumps(data, indent=2))

    # 6. ---- Tools & Add-ons ----
    tool_files = {
        "fingered_gripper.json": fingered_gripper_aas(),
        "suction_gripper.json":  suction_gripper_aas(),
        "screwdriver.json":      screwdriver_aas(),
    }
    for fname, data in tool_files.items():
        (tools_dir / fname).write_text(json.dumps(data, indent=2))

    addon_files = {
        "addon_table.json":    addon_table_aas(),
        "addon_lift.json":     addon_lift_aas(),
        "addon_conveyor.json": addon_conveyor_aas(),
    }
    for fname, data in addon_files.items():
        (addons_dir / fname).write_text(json.dumps(data, indent=2))

    # 7. ---- Products (Flashlight assembly and components) ----
    parts = [
        ("flashlight_housing", "Flashlight Housing",      "PROD_PART_HOUSING", "-Housing"),
        ("flashlight_cover",   "Flashlight Bottom Cover", "PROD_PART_COVER",   "-Cover"),
        ("flashlight_pcb",     "Flashlight LED PCB",      "PROD_PART_PCB",     "-PCB"),
        ("flashlight_lens",    "Flashlight Optical Lens", "PROD_PART_LENS",    "-Lens"),
        ("flashlight_screw",   "Flashlight M3 Screw",     "PROD_PART_SCREW",   "-Screw"),
    ]
    for pslug, pname, pident, paspect in parts:
        pdata = product_aas(pslug, pname, pident, paspect)
        (products_dir / f"{pslug}.json").write_text(json.dumps(pdata, indent=2))

    flashlight_components = [
        ("urn:unimaas:infraflex:product:flashlight_housing", 1),
        ("urn:unimaas:infraflex:product:flashlight_cover", 1),
        ("urn:unimaas:infraflex:product:flashlight_pcb", 1),
        ("urn:unimaas:infraflex:product:flashlight_lens", 1),
        ("urn:unimaas:infraflex:product:flashlight_screw", 4),
    ]
    flashlight_data = product_aas(
        slug="flashlight",
        product_name="UniMaaS LED Flashlight",
        ident="PROD_FLASHLIGHT_001",
        aspect_desig="-FlashlightAssembly",
        components=flashlight_components,
        requires_process_id="urn:unimaas:infraflex:process:flashlight",
    )
    (products_dir / "flashlight.json").write_text(json.dumps(flashlight_data, indent=2))

    # 8. ---- Logistic Routes (AMR transport between cells and warehouse) ----
    routes = [
        ("route_cell1_to_cell5", "Cell 1 to Cell 5 Logistics Route", "ROUTE_CELL1_CELL5",
         "urn:unimaas:infraflex:site:cell1", "urn:unimaas:infraflex:site:cell5",
         "urn:unimaas:infraflex:device:amr_mir200_1", 15.0),
        ("route_cell5_to_cell6", "Cell 5 to Cell 6 Logistics Route", "ROUTE_CELL5_CELL6",
         "urn:unimaas:infraflex:site:cell5", "urn:unimaas:infraflex:site:cell6",
         "urn:unimaas:infraflex:device:amr_mir200_1", 12.5),
        ("route_cell6_to_wh", "Cell 6 to Warehouse Logistics Route", "ROUTE_CELL6_WH",
         "urn:unimaas:infraflex:site:cell6", "urn:unimaas:infraflex:device:warehouse_003",
         "urn:unimaas:infraflex:device:amr_mir500_1", 25.0),
    ]
    for rslug, rname, rident, start_s, end_s, res_id, dist in routes:
        rdata = logistic_route_aas(rslug, rname, rident, start_s, end_s, res_id, dist)
        (routes_dir / f"{rslug}.json").write_text(json.dumps(rdata, indent=2))

    print(f"\nSuccessfully generated full AAS transformation:")
    print(f"  • Tools:            {len(tool_files)} ({tools_dir})")
    print(f"  • Add-ons:          {len(addon_files)} ({addons_dir})")
    print(f"  • Sites (Cells):    {len(cells)} ({cells_dir})")
    print(f"  • Devices:          {executor_count} ({executors_dir})")
    print(f"  • Sensors:          {sensor_count} ({sensors_dir})")
    print(f"  • Characteristics:  {char_count + len(tool_chars) + 6} ({characteristics_dir})")
    print(f"  • Products:         {len(parts) + 1} ({products_dir})")
    print(f"  • Logistic Routes:  {len(routes)} ({routes_dir})")


if __name__ == "__main__":
    main()
