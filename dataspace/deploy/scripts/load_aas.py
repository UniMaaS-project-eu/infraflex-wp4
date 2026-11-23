#!/usr/bin/env python3
"""
Sanitizes and loads all AAS shells, submodels, and concept descriptions from
the cloned repos/unimaas-infraflex/AAS/ directory into BaSyx AAS Environment (:8081)
and registers all descriptors in AAS Registry (:8082) and Submodel Registry (:8083).

Uses valid AAS V3 Part 2 AssetAdministrationShellDescriptor fields (globalAssetId,
assetKind, assetType, specificAssetIds — NOT assetInformation, which causes HTTP 400
in eclipsebasyx/aas-registry-log-mongodb:2.0.0-SNAPSHOT).
"""

import base64
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request


def b64url(val: str) -> str:
    return base64.urlsafe_b64encode(val.encode("utf-8")).decode("ascii").rstrip("=")


def clean_aas_v3(obj, in_sml=False):
    if isinstance(obj, dict):
        if obj.get("modelType") == "Qualifier":
            del obj["modelType"]
        if in_sml and "idShort" in obj:
            del obj["idShort"]
        is_sml = obj.get("modelType") == "SubmodelElementList"
        for k, v in list(obj.items()):
            if is_sml and k == "value" and isinstance(v, list):
                for item in v:
                    clean_aas_v3(item, in_sml=True)
            else:
                clean_aas_v3(v, in_sml=False)
    elif isinstance(obj, list):
        for item in obj:
            clean_aas_v3(item, in_sml=False)


def sanitize_deployed_basyx_folder(basyx_aas_dir: str):
    if not os.path.isdir(basyx_aas_dir):
        return
    seen_cds = set()
    for fpath in sorted(glob.glob(os.path.join(basyx_aas_dir, "*.json"))):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            clean_aas_v3(data)
            cds = data.get("conceptDescriptions", [])
            if cds:
                unique_cds = []
                for cd in cds:
                    cid = cd.get("id")
                    if cid and cid not in seen_cds:
                        seen_cds.add(cid)
                        unique_cds.append(cd)
                data["conceptDescriptions"] = unique_cds
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except Exception as exc:
            print(f"[load_aas] Warning sanitizing {fpath}: {exc}")

    for aasx_path in sorted(glob.glob(os.path.join(basyx_aas_dir, "*.aasx"))):
        json_twin = aasx_path[:-5] + ".json"
        if os.path.exists(json_twin):
            try:
                os.remove(aasx_path)
            except OSError:
                pass


def http_req(method: str, url: str, payload: dict | None = None) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body
    except Exception as exc:
        return 0, str(exc)


def upsert_resource(base_url: str, collection: str, item: dict) -> bool:
    item_id = item.get("id")
    if not item_id:
        return False
    enc_id = b64url(item_id)
    code, body = http_req("POST", f"{base_url}/{collection}", item)
    if code in (200, 201):
        return True
    if code in (400, 409):
        # Try PUT by base64url ID (handles 409 Conflict or existing descriptor)
        put_code, put_body = http_req("PUT", f"{base_url}/{collection}/{enc_id}", item)
        if put_code in (200, 201, 204):
            return True
        # If PUT failed, try DELETE then POST
        http_req("DELETE", f"{base_url}/{collection}/{enc_id}")
        post_code, post_body = http_req("POST", f"{base_url}/{collection}", item)
        if post_code in (200, 201):
            return True
        print(f"[load_aas] Failed upserting {collection} '{item_id}': HTTP {post_code} ({post_body[:160]})")
        return False
    print(f"[load_aas] Failed POST {collection} '{item_id}': HTTP {code} ({body[:160]})")
    return False


def build_shell_descriptor(sh: dict, public_env_url: str, sm_descriptors: list[dict]) -> dict:
    """Build an AAS V3 Part 2 compliant AssetAdministrationShellDescriptor."""
    sh_id = sh["id"]
    asset_info = sh.get("assetInformation") or {}
    desc: dict = {
        "id": sh_id,
        "endpoints": [
            {
                "interface": "AAS-3.0",
                "protocolInformation": {
                    "href": f"{public_env_url}/shells/{b64url(sh_id)}",
                    "endpointProtocol": "HTTP",
                },
            }
        ],
        "submodelDescriptors": sm_descriptors,
    }
    if sh.get("idShort"):
        desc["idShort"] = sh["idShort"]
    if sh.get("description"):
        desc["description"] = sh["description"]
    if sh.get("displayName"):
        desc["displayName"] = sh["displayName"]
    if asset_info.get("assetKind") in ("Instance", "NotApplicable", "Type"):
        desc["assetKind"] = asset_info["assetKind"]
    if asset_info.get("globalAssetId"):
        desc["globalAssetId"] = asset_info["globalAssetId"]
    if asset_info.get("assetType"):
        desc["assetType"] = asset_info["assetType"]
    if asset_info.get("specificAssetIds"):
        desc["specificAssetIds"] = asset_info["specificAssetIds"]
    return desc


def main():
    aas_dir = sys.argv[1] if len(sys.argv) > 1 else "./repos/unimaas-infraflex/AAS"
    repo_root = os.path.dirname(aas_dir.rstrip("/\\"))
    basyx_aas_dir = os.path.join(repo_root, "basyx-setup", "aas")
    sanitize_deployed_basyx_folder(basyx_aas_dir)

    external_host = os.environ.get("EXTERNAL_HOST", "localhost")
    env_port = os.environ.get("AAS_ENV_PORT", "8081")
    reg_port = os.environ.get("AAS_REGISTRY_PORT", "8082")
    sm_reg_port = os.environ.get("SM_REGISTRY_PORT", "8083")

    local_env_url = f"http://localhost:{env_port}"
    public_env_url = f"http://{external_host}:{env_port}"
    local_reg_url = f"http://localhost:{reg_port}"
    local_sm_reg_url = f"http://localhost:{sm_reg_port}"

    # Update basyx.externalurl in basyx-setup/basyx/aas-env.properties so auto-registration uses public_env_url
    props_path = os.path.join(repo_root, "basyx-setup", "basyx", "aas-env.properties")
    if os.path.isfile(props_path):
        try:
            with open(props_path, "r", encoding="utf-8") as f:
                props_txt = f.read()
            new_props = []
            for line in props_txt.splitlines():
                if line.startswith("basyx.externalurl="):
                    new_props.append(f"basyx.externalurl={public_env_url}")
                else:
                    new_props.append(line)
            with open(props_path, "w", encoding="utf-8") as f:
                f.write("\n".join(new_props) + "\n")
        except Exception:
            pass

    print(f"[load_aas] Waiting for BaSyx AAS Environment ({local_env_url}) & Registry ({local_reg_url})...")
    for _ in range(30):
        c1, _ = http_req("GET", f"{local_env_url}/shells")
        c2, _ = http_req("GET", f"{local_reg_url}/shell-descriptors")
        if c1 in (200, 204) and c2 in (200, 204):
            break
        time.sleep(2)
    else:
        print("[load_aas] WARNING: BaSyx AAS Environment or Registry did not become reachable in time.")
        return 1

    subdirs = [
        "addons",
        "arms",
        "cells",
        "characteristics",
        "executors",
        "processes",
        "products",
        "routes",
        "sensors",
        "tools",
    ]

    shells_ok = 0
    reg_shells_ok = 0
    sms_ok = 0
    reg_sms_ok = 0
    cds_ok = 0
    seen_cds = set()

    for sd in subdirs:
        pattern = os.path.join(aas_dir, sd, "*.json")
        files = sorted(glob.glob(pattern))
        if not files:
            continue
        for fpath in files:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                print(f"[load_aas] Skipping invalid JSON {fpath}: {exc}")
                continue

            clean_aas_v3(data)

            # 1. Concept Descriptions
            for cd in data.get("conceptDescriptions", []):
                cid = cd.get("id")
                if cid and cid not in seen_cds:
                    seen_cds.add(cid)
                    if upsert_resource(local_env_url, "concept-descriptions", cd):
                        cds_ok += 1

            # 2. Submodels + Submodel Registry Descriptors
            sm_descriptors = []
            for sm in data.get("submodels", []):
                sm_id = sm.get("id")
                if not sm_id:
                    continue
                if upsert_resource(local_env_url, "submodels", sm):
                    sms_ok += 1
                sm_desc = {
                    "id": sm_id,
                    "endpoints": [
                        {
                            "interface": "SUBMODEL-3.0",
                            "protocolInformation": {
                                "href": f"{public_env_url}/submodels/{b64url(sm_id)}",
                                "endpointProtocol": "HTTP",
                            },
                        }
                    ],
                }
                if sm.get("idShort"):
                    sm_desc["idShort"] = sm["idShort"]
                if sm.get("semanticId"):
                    sm_desc["semanticId"] = sm["semanticId"]
                sm_descriptors.append(sm_desc)
                if upsert_resource(local_sm_reg_url, "submodel-descriptors", sm_desc):
                    reg_sms_ok += 1

            # 3. Asset Administration Shells + AAS Registry Descriptors
            for sh in data.get("assetAdministrationShells", []):
                sh_id = sh.get("id")
                if not sh_id:
                    continue
                if upsert_resource(local_env_url, "shells", sh):
                    shells_ok += 1
                sh_desc = build_shell_descriptor(sh, public_env_url, sm_descriptors)
                if upsert_resource(local_reg_url, "shell-descriptors", sh_desc):
                    reg_shells_ok += 1

    print(
        f"[load_aas] Completed:\n"
        f"  - AAS Repository (:8081):      {shells_ok} shells, {sms_ok} submodels, {cds_ok} concept descriptions\n"
        f"  - AAS Registry (:8082):        {reg_shells_ok} shell-descriptors registered\n"
        f"  - Submodel Registry (:8083):   {reg_sms_ok} submodel-descriptors registered\n"
        f"  - Public Endpoint Base URL:    {public_env_url}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
