"""
WoT Thing Description templates for each Infraflex executor device type.

Each template follows the W3C WoT Thing Description 1.1 specification.
These are used by vo_manager.py to produce ExposedThings via wotpy2.
"""

WOT_CONTEXT = "https://www.w3.org/2019/wot/td/v1"

INFRAFLEX_ONTOLOGY_BASE = "http://infraflex.eu/ontology#"


def _base_properties():
    """Properties shared by all device types."""
    return {
        "OperationalState": {
            "title": "Operational State",
            "type": "string",
            "description": "Device operational state (Idle, Executing, Booting, Error)",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}operationalState"
        },
        "CurrentSkill": {
            "title": "Current Skill",
            "type": "string",
            "description": "Name of the skill currently being executed",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}currentSkill"
        },
        "TaskState": {
            "title": "Task State",
            "type": "string",
            "description": "State of the current task (Done, Failed, Executing)",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}taskState"
        },
        "LastUpdate": {
            "title": "Last Update",
            "type": "string",
            "description": "ISO 8601 timestamp of the last MQTT message received",
            "observable": False,
            "readOnly": True
        }
    }


def powerair_td(vo_id: str, device_path: str) -> dict:
    """Thing Description for a PowerAir executor (gripper + power control)."""
    props = _base_properties()
    props.update({
        "GripperClose": {
            "title": "Gripper Close",
            "type": "boolean",
            "description": "Whether the gripper is currently closed",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}gripperClose"
        },
        "ToolAttach": {
            "title": "Tool Attached",
            "type": "boolean",
            "description": "Whether a tool is currently attached",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}toolAttach"
        },
        "AirErrorCode": {
            "title": "Air System Error Code",
            "type": "integer",
            "description": "Error code from the pneumatic air subsystem",
            "observable": True,
            "readOnly": True
        },
        "AirErrorMsg": {
            "title": "Air System Error Message",
            "type": "string",
            "description": "Human-readable error message from the air subsystem",
            "observable": True,
            "readOnly": True
        },
        "PowerToolEnable": {
            "title": "Power Tool Enable",
            "type": "boolean",
            "description": "Whether the powered tool is enabled",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}toolEnable"
        },
        "TaskDuration": {
            "title": "Task Duration",
            "type": "number",
            "description": "Duration of the last completed task in seconds",
            "observable": False,
            "readOnly": True
        }
    })
    return {
        "@context": WOT_CONTEXT,
        "id": f"urn:infraflex:vo:{vo_id}",
        "title": f"Infraflex {device_path.replace('/', ' ')}",
        "description": f"Virtual Object for Infraflex PowerAir executor at {device_path}",
        "@type": [f"{INFRAFLEX_ONTOLOGY_BASE}PowerAirExecutor"],
        "properties": props
    }


def lift_td(vo_id: str, device_path: str) -> dict:
    """Thing Description for a Lift executor."""
    props = _base_properties()
    props.update({
        "Position": {
            "title": "Lift Position",
            "type": "string",
            "description": "Current named position of the lift (e.g. 'work', 'service', 'dock')",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}liftPosition"
        },
        "ServiceMode": {
            "title": "Service Mode",
            "type": "boolean",
            "description": "Whether the lift is in service mode",
            "observable": True,
            "readOnly": True
        },
        "ErrorCode": {
            "title": "Error Code",
            "type": "integer",
            "description": "Device error code (0 = no error)",
            "observable": True,
            "readOnly": True
        },
        "ErrorMsg": {
            "title": "Error Message",
            "type": "string",
            "description": "Human-readable error message",
            "observable": True,
            "readOnly": True
        },
        "ControlStop": {
            "title": "Control Stop Active",
            "type": "boolean",
            "description": "Whether the control stop signal is active",
            "observable": True,
            "readOnly": True
        },
        "EmergencyStop": {
            "title": "Emergency Stop Active",
            "type": "boolean",
            "description": "Whether an emergency stop is active",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}emergencyStop"
        },
        "LoweringEnable": {
            "title": "Lowering Enabled",
            "type": "boolean",
            "description": "Whether lowering motion is enabled",
            "observable": True,
            "readOnly": True
        }
    })
    return {
        "@context": WOT_CONTEXT,
        "id": f"urn:infraflex:vo:{vo_id}",
        "title": f"Infraflex {device_path.replace('/', ' ')}",
        "description": f"Virtual Object for Infraflex Lift executor at {device_path}",
        "@type": [f"{INFRAFLEX_ONTOLOGY_BASE}LiftExecutor"],
        "properties": props
    }


def turntable_td(vo_id: str, device_path: str) -> dict:
    """Thing Description for a Turntable executor."""
    props = _base_properties()
    props.update({
        "SlotEmpty": {
            "title": "Slot Empty Flags",
            "type": "string",
            "description": "JSON array indicating which slots are empty (e.g. '[true, false, true]')",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}slotEmpty"
        },
        "SlotPositions": {
            "title": "Slot Positions",
            "type": "string",
            "description": "JSON array of current slot position identifiers",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}slotPositions"
        },
        "ErrorCode": {
            "title": "Error Code",
            "type": "integer",
            "description": "Device error code (0 = no error)",
            "observable": True,
            "readOnly": True
        },
        "ErrorMsg": {
            "title": "Error Message",
            "type": "string",
            "description": "Human-readable error message",
            "observable": True,
            "readOnly": True
        }
    })
    return {
        "@context": WOT_CONTEXT,
        "id": f"urn:infraflex:vo:{vo_id}",
        "title": f"Infraflex {device_path.replace('/', ' ')}",
        "description": f"Virtual Object for Infraflex Turntable executor at {device_path}",
        "@type": [f"{INFRAFLEX_ONTOLOGY_BASE}TurntableExecutor"],
        "properties": props
    }


def amr_td(vo_id: str, device_path: str) -> dict:
    """Thing Description for an AMR (Autonomous Mobile Robot) executor."""
    props = _base_properties()
    props.update({
        "BatteryLevel": {
            "title": "Battery Level",
            "type": "number",
            "description": "Battery charge level in percent (0-100)",
            "unit": "%",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}batteryLevel"
        },
        "CurrentLocation": {
            "title": "Current Location",
            "type": "string",
            "description": "Named location where the AMR is currently stationed or heading",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}currentLocation"
        },
        "PositionX": {
            "title": "Position X",
            "type": "number",
            "description": "X coordinate on the shopfloor map",
            "unit": "m",
            "observable": True,
            "readOnly": True
        },
        "PositionY": {
            "title": "Position Y",
            "type": "number",
            "description": "Y coordinate on the shopfloor map",
            "unit": "m",
            "observable": True,
            "readOnly": True
        },
        "Orientation": {
            "title": "Orientation",
            "type": "number",
            "description": "Heading angle in radians",
            "unit": "rad",
            "observable": True,
            "readOnly": True
        },
        "ErrorCode": {
            "title": "Error Code",
            "type": "integer",
            "description": "Device error code (0 = no error)",
            "observable": True,
            "readOnly": True
        },
        "ErrorMsg": {
            "title": "Error Message",
            "type": "string",
            "description": "Human-readable error message",
            "observable": True,
            "readOnly": True
        }
    })
    return {
        "@context": WOT_CONTEXT,
        "id": f"urn:infraflex:vo:{vo_id}",
        "title": f"Infraflex {device_path.replace('/', ' ')}",
        "description": f"Virtual Object for Infraflex AMR at {device_path}",
        "@type": [f"{INFRAFLEX_ONTOLOGY_BASE}AMRExecutor"],
        "properties": props
    }


def warehouse_td(vo_id: str, device_path: str) -> dict:
    """Thing Description for a Warehouse executor."""
    props = _base_properties()
    props.update({
        "ErrorCode": {
            "title": "Error Code",
            "type": "integer",
            "description": "Device error code (0 = no error)",
            "observable": True,
            "readOnly": True
        },
        "ErrorMsg": {
            "title": "Error Message",
            "type": "string",
            "description": "Human-readable error message",
            "observable": True,
            "readOnly": True
        },
        "EmergencyStop": {
            "title": "Emergency Stop Active",
            "type": "boolean",
            "description": "Whether an emergency stop is active",
            "observable": True,
            "readOnly": True,
            "semanticType": f"{INFRAFLEX_ONTOLOGY_BASE}emergencyStop"
        },
        "IOStatus": {
            "title": "24V I/O Status",
            "type": "boolean",
            "description": "Status of the 24V I/O module",
            "observable": True,
            "readOnly": True
        }
    })
    return {
        "@context": WOT_CONTEXT,
        "id": f"urn:infraflex:vo:{vo_id}",
        "title": f"Infraflex {device_path.replace('/', ' ')}",
        "description": f"Virtual Object for Infraflex Warehouse executor at {device_path}",
        "@type": [f"{INFRAFLEX_ONTOLOGY_BASE}WarehouseExecutor"],
        "properties": props
    }


def debonder_td(vo_id: str, device_path: str) -> dict:
    """Thing Description for a Debonder executor."""
    props = _base_properties()
    props.update({
        "ErrorCode": {
            "title": "Error Code",
            "type": "integer",
            "description": "Device error code (0 = no error)",
            "observable": True,
            "readOnly": True
        },
        "ErrorMsg": {
            "title": "Error Message",
            "type": "string",
            "description": "Human-readable error message",
            "observable": True,
            "readOnly": True
        }
    })
    return {
        "@context": WOT_CONTEXT,
        "id": f"urn:infraflex:vo:{vo_id}",
        "title": f"Infraflex {device_path.replace('/', ' ')}",
        "description": f"Virtual Object for Infraflex Debonder executor at {device_path}",
        "@type": [f"{INFRAFLEX_ONTOLOGY_BASE}DebonderExecutor"],
        "properties": props
    }


DEVICE_TYPE_TO_TD = {
    "PowerAir": powerair_td,
    "Lift": lift_td,
    "Turntable": turntable_td,
    "AMR": amr_td,
    "Warehouse": warehouse_td,
    "Debonder": debonder_td,
}


def get_td(device_type: str, vo_id: str, device_path: str) -> dict:
    """Return the Thing Description for the given device type."""
    builder = DEVICE_TYPE_TO_TD.get(device_type)
    if builder is None:
        raise ValueError(f"Unknown device type: {device_type!r}")
    td = builder(vo_id, device_path)
    td.setdefault("securityDefinitions", {"nosec_sc": {"scheme": "nosec"}})
    td.setdefault("security", ["nosec_sc"])
    return td
