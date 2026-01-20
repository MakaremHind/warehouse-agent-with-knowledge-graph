# tools.py
# -----------------------------------------------------------------------------
# Warehouse Chat Tools
# -----------------------------------------------------------------------------
# This module provides a set of tools for interacting with the warehouse system.
# It includes MQTT communication, module/box lookup, order dispatch, and wrappers
# for MRKL/agent compatibility. All logic is preserved as in the original code.
# -----------------------------------------------------------------------------

from typing import Dict, Any, List
import logging, json, time, uuid, threading
import paho.mqtt.client as mqtt
from langchain_core.tools import tool
from models import Envelope, normalize_message
from mqtt_listener import get, BROKER_CONNECTED, LAST_MASTER_MSG
from snapshot_manager import snapshot_store   # keeps type checkers happy
from rapidfuzz import process
from knowledge_graph.kg_keeper import KGKeeper
from knowledge_graph.kg_logger import log_transport_operation

# MQTT CONFIGURATION
BROKER  = "localhost"
PORT    = 1883
ORDER_REQUEST_TOPIC        = "base_01/order_request"
ORDER_RESPONSE_BASE_TOPIC  = "base_01/order_request/response"
kg = KGKeeper("WarehouseKG.rdf")

# SHARED STATE
_order_results: Dict[str, Dict[str, Any]] = {}
_result_listener_started = False
cancelled_orders = set()
current_order_id  = None

# TIMEOUTS
ONLINE_TIMEOUT = 30.0      # seconds without a master message → “offline”

# -----------------------------------------------------------------------------
# INTERNAL UTILITIES
# -----------------------------------------------------------------------------
def _nf(entity: str, key: Any) -> Dict[str, Any]:
    """Return a standard ‘not-found’ payload."""
    return {"found": False, "error": f"{entity} '{key}' not found"}


def _iter_modules():
    """Yield all modules from the current snapshot (normalized or raw)."""
    env = get("base_01/base_module_visualization")
    if not env:
        return []
    # accept either normalised form (“items”) or raw form (“modules”)
    return (
        env.data.get("items")                 # preferred, after normalisation
        or env.data.get("modules", [])        # raw, just in case
    )

# -----------------------------------------------------------------------------
# TOOL DEFINITIONS (LangChain @tool)
# -----------------------------------------------------------------------------
@tool
def master_status() -> dict:
    """
    Check if the Master controller is online based on the latest 'master/state' message.
    """
    payload = snapshot_store.get("master/state")
    if not payload:
        return {
            "online": False,
            "info": "No message received from master/state"
        }

    status = str(payload.get("data", "")).lower()
    is_online = status == "online"

    return {
        "online": is_online,
        "info": f"Master state: {status}"
    }


def _pose_from_module(namespace: str):
    modules = _iter_modules()
    print(f"[DEBUG] Looking for module '{namespace}' in {[m['namespace'] for m in modules]}")
    if not modules:
        raise ValueError("No modules available in base_01/base_module_visualization snapshot")

    for m in modules:
        if m["namespace"] == namespace:
            return m["pose"]

    raise ValueError(f"Module '{namespace}' not found")


def _start_result_listener():
    def on_message(client, userdata, msg):
        payload = json.loads(msg.payload.decode())
        cid = payload.get("header", {}).get("correlation_id")

        if cid in cancelled_orders and not payload.get("_republished", False):
            print(f"[listener] ⚠ Ignoring response for canceled order {cid}")
            return

        _order_results[cid] = payload

        # 🔍 Check success status
        status = "SUCCESS" if payload.get("success", False) else "FAILED"
        print(f"[listener] Got result for order {cid} — {status}")

    client = mqtt.Client()
    client.on_message = on_message
    client.connect(BROKER, PORT)
    client.subscribe(f"{ORDER_RESPONSE_BASE_TOPIC}/#")
    threading.Thread(target=client.loop_forever, daemon=True).start()
    

@tool(args_schema={"start": str, "goal": str})
def plan_path(start: str, goal: str) -> List[str]:
    """
    Plan a valid transport path between warehouse modules.
    (see docstring for full rules)
    """

    modules = _iter_modules()
    poses = {m["namespace"]: m["pose"] for m in modules}

    if start not in poses:
        raise ValueError(f"Start module '{start}' not found.")
    if goal not in poses:
        raise ValueError(f"Goal module '{goal}' not found.")

    start_pose = poses[start]
    goal_pose = poses[goal]

    def euclidean(p1, p2):
        return ((p1["x"] - p2["x"])**2 + (p1["y"] - p2["y"])**2) ** 0.5

    # Get available modules
    uarms = [m for m in modules if m["namespace"].startswith("uarm")]
    docks = [m for m in modules if m["namespace"].startswith("dock")]

    # Try direct uArm route first
    distance = euclidean(start_pose, goal_pose)
    if distance < 500:
        # Choose closest uArm to start
        nearest_uarm = min(uarms, key=lambda u: euclidean(u["pose"], start_pose))
        return [start, nearest_uarm["namespace"], goal]

    # Too far → use turtlebot leg between docks (docks not shown in path)
    dock_start = min(docks, key=lambda d: euclidean(start_pose, d["pose"]))
    dock_goal  = min(docks, key=lambda d: euclidean(goal_pose,  d["pose"]))

    # uArm to connect to turtlebot at each end
    uarm_start = min(uarms, key=lambda u: euclidean(u["pose"], start_pose))
    uarm_goal  = min(uarms, key=lambda u: euclidean(u["pose"], goal_pose))

    # Placeholder turtlebot
    turtlebot = "turtlebot_01"

    return [start, uarm_start["namespace"], turtlebot, uarm_goal["namespace"], goal]


@tool
def list_boxes() -> list:
    """Return `[id, color, type]` for every detected box (no pose)."""
    env = get("mmh_cam/detected_boxes")
    if not env:
        return []
    return [{"id": i, "color": b["color"], "type": b["type"]}
            for i, b in enumerate(env.data["boxes"])]

@tool(args_schema={"box_id": int})
def find_box(box_id: int):
    """Find a box by index in the list and return full box data including pose."""
    env = get("mmh_cam/detected_boxes")
    if not env or not env.data["boxes"]:
        return _nf("box", box_id)
    if 0 <= box_id < len(env.data["boxes"]):
        return {"found": True, **env.data["boxes"][box_id]}
    return _nf("box", box_id)

@tool(args_schema={"color": str})
def find_box_by_color(color: str):
    """
    Return **all** boxes with the matching color, including their poses.
    If none found, returns `found: False`.
    """
    env = get("mmh_cam/detected_boxes")
    if not env or not env.data.get("boxes"):
        return _nf("box(color)", color)

    matching = [
        {"id": i, **b}
        for i, b in enumerate(env.data["boxes"])
        if b.get("color", "").lower() == color.lower()
    ]

    if not matching:
        return _nf("box(color)", color)

    return {
        "found": True,
        "count": len(matching),
        "boxes": matching
    }


@tool
def list_modules() -> List[str]:
    """Returns the list of all available module namespaces (e.g., conveyors, containers, docks, etc.)"""
    snapshot = get("base_01/base_module_visualization")
    if not snapshot:
        return []

    # Accept normalized or raw format
    modules = (
        snapshot.data.get("items")
        or snapshot.data.get("modules", [])
    )
    return [m["namespace"] for m in modules if "namespace" in m]

@tool(args_schema={"namespace": str})
def find_module(namespace: str):
    """Find a module by namespace and return its pose and attributes."""
    env = get("base_01/base_module_visualization")
    if not env:
        return _nf("modules", namespace)

    # Try both key options
    modules_list = []
    if "items" in env.data:
        modules_list = env.data["items"]
    elif "modules" in env.data:
        modules_list = env.data["modules"]

    print("[DEBUG] Found modules:", [m["namespace"] for m in modules_list])

    for m in modules_list:
        if m["namespace"] == namespace:
            return {"found": True, **m}

    return _nf("module", namespace)


@tool(args_schema={"x": float, "y": float})
def find_closest_module(*, x: float, y: float) -> Dict[str, Any]:
    """
    Determine which warehouse module a given (x, y) mm point is in.
    Uses rectangular footprint containment first; falls back to distance.
    """
    modules = _iter_modules()
    if not modules:
        return {"found": False,
                "error": "No modules available in base_01/base_module_visualization"}

    def euclidean(p1, p2):
        return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2) ** 0.5

    def module_type(namespace: str) -> str:
        if namespace.startswith("conveyor"):
            return "conveyor"
        if namespace.startswith("container"):
            return "container"
        if namespace.startswith("uarm"):
            return "uarm"
        if namespace.startswith("dock"):
            return "dock"
        return "unknown"

    def is_inside(px, py, cx, cy, width, height):
        return abs(px - cx) <= width / 2 and abs(py - cy) <= height / 2

    # Realistic footprint estimates (W×H in mm)
    FOOTPRINT_MM = {
        "conveyor": (450, 150),     # long horizontal
        "container": (150, 150),
        "uarm": (200, 200),
        "dock": (200, 200)
    }

    # 1. Check if point lies inside any module footprint
    for m in modules:
        ns = m["namespace"]
        pose = m["pose"]
        typ = module_type(ns)
        w, h = FOOTPRINT_MM.get(typ, (150, 150))  # default to container size

        if is_inside(x, y, pose["x"], pose["y"], w, h):
            print(f"[DEBUG] Point is INSIDE {ns} (type={typ})")
            return {
                "found": True,
                "namespace": ns,
                "method": "footprint",
                "distance": 0.0
            }

    # 2. Fallback to closest center if no match
    target = (x, y)
    best_mod, best_dist = None, float("inf")
    for m in modules:
        pose = m["pose"]
        dist = euclidean(target, (pose["x"], pose["y"]))
        if dist < best_dist:
            best_mod, best_dist = m, dist

    return {
        "found": True,
        "namespace": best_mod["namespace"],
        "method": "distance",
        "distance": best_dist
    }


# ── list every order response currently cached ────────────────────────────
@tool
def list_orders() -> dict:
    """
    Return **all** order-response payloads held in `snapshot_store`
    (newest first).
    """
    orders = []
    for topic in snapshot_store.snapshots:
        if topic.startswith("base_01/order_request/response"):
            payload = snapshot_store.get(topic)
            if payload:
                orders.append(payload)

    if not orders:
        return {"found": False,
                "error": "No order responses present in snapshot_store."}

    orders.sort(key=lambda p: p.get("header", {}).get("timestamp", 0),
                reverse=True)
    return {"found": True, "orders": orders}



@tool
def find_last_order(args: dict = {}):
    """Returns the most recent TransportOperation from the knowledge graph."""
    op = kg.get_last_operation()
    if not op:
        return {"found": False, "error": "No operations logged in the KG."}

    op_iri, _ = op
    details = kg.get_operation_details(op_iri)

    if not details:
        return {"found": False, "error": "Could not retrieve operation details."}

    return {
        "found": True,
        "operation": details
    }

    
# ── unified trigger_order tool ────────────────────────────────────────────
@tool(args_schema={
    "start":       str,
    "goal":        str,
    "start_pose":  dict,
    "goal_pose":   dict,

    # optional cargo-box overrides
    "box_id":      int,
    "box_color":   str,
    "box_pose":    dict,

    # optional – how long (s) to wait for a response
    "wait_timeout": int
})
def trigger_order(
    *,
    start: str | None = None,
    goal: str | None = None,
    start_pose: dict | None = None,
    goal_pose: dict  | None = None,
    box_id:    int   | None = None,
    box_color: str   | None = None,
    box_pose:  dict  | None = None,
) -> dict:
    """
    Dispatch a transport order **and block up to 60 s** for a response.

    Required (pick one in each row):
    • `start`      **or** `start_pose`
    • `goal`       **or** `goal_pose`

    Optional cargo-box overrides: `box_id`, `box_color`, `box_pose`.
    """

    # ── 0. make sure the background listener runs ────────────────────
    global _result_listener_started, current_order_id
    if not _result_listener_started:
        _start_result_listener()
        _result_listener_started = True

    # ── 1. resolve start / goal poses ─────────────────────────────────
    try:
        if start is not None:
            start_pose_val, start_ns = _pose_from_module(start), start
        elif start_pose is not None:
            start_pose_val, start_ns = start_pose, "manual_pose_start"
        else:
            raise ValueError("provide either 'start' or 'start_pose'")

        if goal is not None:
            goal_pose_val, goal_ns = _pose_from_module(goal), goal
        elif goal_pose is not None:
            goal_pose_val, goal_ns = goal_pose, "manual_pose_goal"
        else:
            raise ValueError("provide either 'goal' or 'goal_pose'")

    except ValueError as exc:
        return {"found": False, "error": str(exc)}

    # ── 2. build & publish MQTT payload ───────────────────────────────
    correlation_id   = str(uuid.uuid4())
    current_order_id = correlation_id

    cargo_box = {
        "id":    box_id    if box_id    is not None else 7,
        "color": box_color if box_color is not None else "red",
        "type":  "small",
        "global_pose": box_pose if box_pose is not None else
                       {"x": 0, "y": 0, "z": 0,
                        "roll": 0, "pitch": 0, "yaw": 0}
    }

    payload = {
        "header": {
            "timestamp": time.time(),
            "sender_id": "OrderGenerator",
            "correlation_id": correlation_id
        },
        "starting_module": {"namespace": start_ns, "pose": start_pose_val},
        "goal":            {"namespace": goal_ns,  "pose": goal_pose_val},
        "cargo_box":       cargo_box
    }

    client = mqtt.Client()
    client.connect(BROKER, PORT)
    client.publish(ORDER_REQUEST_TOPIC, json.dumps(payload))
    client.loop(1.0)  # allow time to send
    client.disconnect()

    print(f"[trigger_order] ➡ Dispatched order {correlation_id}")

    # ── 3. wait (max 60 s) for the matching response ──────────────────
    t0 = time.time()
    while time.time() - t0 < 60:
        result = _order_results.get(correlation_id)
        if result:                       # got it!
            success = bool(result.get("success", False))
            log_transport_operation(
                correlation_id=correlation_id,
                start_module=start_ns,
                goal_module=goal_ns,
                box_individual=f"box_{cargo_box['id']}_{cargo_box['color']}",
                success=success,
                timestamp=result["header"]["timestamp"],
            )
            return {
                "found": True,
                "correlation_id": correlation_id,
                "success": success,
                "response": result
            }
        time.sleep(0.5)

    # timed out
    return {
        "found": False,
        "correlation_id": correlation_id,
        "error": "No response within 60 s."
    }


@tool
def confirm_last_order():
    """Report whether the most recent logged TransportOperation succeeded."""
    op = kg.get_last_operation()
    if not op:
        return {"found": False, "error": "No operations found in the knowledge graph."}

    op_iri, _ = op
    details = kg.get_operation_details(op_iri)

    if not details:
        return {"found": False, "error": "Could not read operation details."}

    cid = details["correlation_id"]
    success = details["success"]

    if success:
        msg = f"Order `{cid}` completed successfully."
    else:
        msg = f"Order `{cid}` FAILED."

    return {
        "found": True,
        "operation": details,
        "message": msg
    }



# === Updated diagnose_failure tool ===
from langchain_core.tools import tool
from mqtt_listener import get
from snapshot_manager import snapshot_store

@tool
def diagnose_failure() -> dict:
    """
    Diagnose the reason for a failed order by scanning relevant MQTT log topics,
    regardless of correlation ID.

    It checks:
    - `base_01/*/transport/response` for success=false
    - `master/logs/execute_planned_path` for module execution failures
    - `master/logs/search_for_box_in_starting_module_workspace` for missing boxes
    """

    reasons = []

    for topic, payload in snapshot_store.snapshots.items():
        if not isinstance(payload, dict):
            continue

        msg = json.dumps(payload)

        # --- Topic-specific failure indicators ------------------------

        if "base_01/" in topic and topic.endswith("/transport/response"):
            if not payload.get("success", True):
                reasons.append(f"Transport failure reported in {topic}.")

        elif topic == "master/logs/execute_planned_path":
            message = payload.get("message", "")
            if "Transport failed" in message:
                reasons.append("Transport failed at a module during execution.")

        elif topic == "master/logs/search_for_box_in_starting_module_workspace":
            message = payload.get("message", "")
            if "No box found" in message:
                reasons.append("No box found in starting module workspace.")

    # collapse duplicates
    seen = set()
    unique_reasons = [r for r in reasons if not (r in seen or seen.add(r))]

    if not unique_reasons:
        return {
            "found": False,
            "error": "No known failure messages found in relevant topics."
        }

    return {
        "found": True,
        "reason": "; ".join(unique_reasons)
    }


# -----------------------------------------------------------------------------
# PUBLIC EXPORT
# -----------------------------------------------------------------------------
ALL_TOOLS = [
    find_box,
    find_box_by_color,
    find_module,
    list_boxes,
    find_last_order,
    trigger_order,
    confirm_last_order,
    diagnose_failure,
    list_modules,
    master_status,
    list_orders,
    plan_path,
    find_closest_module
]

# Default log level
logging.getLogger().setLevel(logging.INFO)

# ────────── SINGLE-STRING WRAPPERS for MRKL agent ──────────
from typing import Any, Dict
from langchain.agents import Tool

def _parse_kv(arg: str) -> Dict[str, str]:
    result = {}
    for part in arg.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
        elif "=" in part:
            k, v = part.split("=", 1)
        else:
            continue
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result

# ---- helpers that accept *either* string or dict -----------
def _ensure_dict(inp: Any) -> Dict[str, Any]:
    if isinstance(inp, dict):
        return inp
    if isinstance(inp, str):
        inp = inp.strip()
        try:
            return json.loads(inp)
        except json.JSONDecodeError:
            if "=" in inp or ":" in inp:
                return _parse_kv(inp)
            else:
                return {"namespace": inp}
    raise ValueError("Unsupported input type")



from rapidfuzz import process


# ------------------------------------------------------------
# Helper: get all module names from KG
# ------------------------------------------------------------
def _kg_list_modules():
    """
    Extract all module individual names from ontology.
    These are the local names after the '#':
    Example: http://...#conveyor_02 → 'conveyor_02'
    """
    q = """
    PREFIX w: <http://www.semanticweb.org/hindm/ontologies/2025/11/warehouse_kg#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?m WHERE {
        ?m rdf:type/rdfs:subClassOf* w:Module .
    }
    """
    modules = []
    for row in kg.graph.query(q):
        iri = str(row[0])
        name = iri.split("#")[-1]
        modules.append(name)
    return modules

def _kg_list_box_colors():
    """Returns all boxColor values from ontology."""
    q = """
    PREFIX w: <http://www.semanticweb.org/hindm/ontologies/2025/11/warehouse_kg#>
    SELECT ?color WHERE {
        ?b a w:Box ;
           w:boxColor ?color .
    }
    """
    return list({str(row[0]).lower() for row in kg.graph.query(q)})


def _kg_list_box_ids():
    """Returns all box identifiers based on individual names (box_0, box_1...)."""
    q = """
    PREFIX w: <http://www.semanticweb.org/hindm/ontologies/2025/11/warehouse_kg#>
    SELECT ?b WHERE { ?b a w:Box . }
    """
    out = []
    for row in kg.graph.query(q):
        name = str(row[0]).split("#")[-1]
        if name.startswith("box_"):
            try:
                out.append(int(name.split("_")[1]))
            except:
                pass
    return sorted(out)

def find_closest_module_wrap(arg: Any) -> Dict[str, Any]:
    """
    Wrapper for the `find_closest_module` tool.

    Accepts either…

      • dict  –  { "x": 0.52, "y": 1.34 }
      • str   –  "x=0.52, y=1.34"
    """
    try:
        d = _ensure_dict(arg)

        if "x" not in d or "y" not in d:
            return {"found": False,
                    "error": "find_closest_module expects numeric 'x' and 'y'."}

        d["x"] = float(d["x"])
        d["y"] = float(d["y"])

        return find_closest_module.invoke(d)

    except Exception as e:
        return {"found": False, "error": f"Invalid input: {e}"}
    
    


# ---------- one wrapper per original tool -------------------
def find_box_wrap(arg: Any):
    """
    ✔ Box ID checked using ontology (box_0, box_1…)
    ✔ Fuzzy numeric suggestion
    ✔ Snapshot used for pose retrieval
    """
    d = _ensure_dict(arg)

    try:
        box_id = int(d.get("box_id", arg))
    except:
        return {"found": False, "error": "box_id must be an integer."}

    kg_ids = _kg_list_box_ids()

    if not kg_ids:  # KG has no boxes
        print("[KG] No Box instances found — fallback to snapshot.")
        return find_box.invoke({"box_id": box_id})

    if box_id in kg_ids:
        return find_box.invoke({"box_id": box_id})

    # Suggest nearest ID
    suggestion = min(kg_ids, key=lambda x: abs(x - box_id))

    return {
        "found": False,
        "error": f"Box '{box_id}' is not defined in ontology.",
        "valid_box_ids": kg_ids,
        "did_you_mean": suggestion
    }


def plan_path_wrap(arg: Any) -> dict:
    """
    KG-aware wrapper for plan_path.

    Responsibilities:
    ✔ fuzzy module resolution via KG
    ✔ explicit KG reachability check
    ✔ meaningful failure explanations
    ✔ calls geometric planner only when feasible
    """

    d = _ensure_dict(arg)

    if "start" not in d or "goal" not in d:
        return {
            "found": False,
            "error": "Missing required keys: 'start' and 'goal'."
        }

    # --- Resolve module names using KG ---
    start_info = find_module_wrap(d["start"])
    goal_info  = find_module_wrap(d["goal"])

    if not start_info.get("found"):
        return {"found": False, "error": start_info.get("error")}

    if not goal_info.get("found"):
        return {"found": False, "error": goal_info.get("error")}

    start_ns = start_info["namespace"]
    goal_ns  = goal_info["namespace"]

    # --- Explicit KG reachability check ---
    if not kg.reachable(start_ns, goal_ns):
        neighbors = kg.neighbors(start_ns)

        return {
            "found": False,
            "error": (
                f"No valid path exists in the knowledge graph "
                f"from '{start_ns}' to '{goal_ns}'."
            ),
            "start": start_ns,
            "goal": goal_ns,
            "reachable_neighbors_of_start": neighbors,
            "hint": (
                "Check warehouse topology or choose an intermediate module."
                if neighbors else
                "Start module has no outgoing connections in the KG."
            )
        }

    # --- Optional: state validation (still useful) ---
    ok, reason = kg.validate_operation(start_ns, goal_ns)
    if not ok:
        return {
            "found": False,
            "error": f"KG constraint violation: {reason}"
        }

    # --- Call real geometric planner ---
    path = plan_path.invoke({
        "start": start_ns,
        "goal": goal_ns
    })

    return {
        "found": True,
        "path": path,
        "start": start_ns,
        "goal": goal_ns,
        "validated_by": "knowledge_graph"
    }






def find_box_by_color_wrap(arg: Any):
    """
    ✔ Uses ontology colors first
    ✔ Exact + fuzzy match
    ✔ Snapshot used to return actual poses
    """
    d = _ensure_dict(arg)
    color = d.get("color", str(arg)).lower()

    kg_colors = _kg_list_box_colors()

    # No colors in ontology → fallback
    if not kg_colors:
        print("[KG] No color definitions — fallback to snapshot.")
        return find_box_by_color.invoke({"color": color})

    # Exact match
    if color in kg_colors:
        return find_box_by_color.invoke({"color": color})

    # Fuzzy match
    match = process.extractOne(color, kg_colors)
    if match:
        best, score, _ = match
        if score >= 80:
            print(f"[KG] Color fuzzy match '{color}' → '{best}'")
            return find_box_by_color.invoke({"color": best})
        suggestion = best
    else:
        suggestion = None

    return {
        "found": False,
        "error": f"Color '{color}' is not defined in ontology.",
        "valid_colors": kg_colors,
        "did_you_mean": suggestion
    }



def find_module_wrap(arg: Any):
    """
    Updated wrapper:
      ✔ checks module individuals using new ontology
      ✔ extracts names from IRIs (no moduleNamespace)
      ✔ fuzzy matching allowed
      ✔ returns real snapshot module data
    """
    d = _ensure_dict(arg)
    target = d.get("namespace", str(arg)).strip()

    # --- KG list of module names ---
    kg_modules = _kg_list_modules()

    # KG has no modules → fallback to snapshot
    if not kg_modules:
        print("[KG] No module individuals found — fallback to snapshot.")
        return find_module.invoke({"namespace": target})

    target_lower = target.lower()
    kg_lower = [m.lower() for m in kg_modules]

    # --- Exact match ---
    if target_lower in kg_lower:
        idx = kg_lower.index(target_lower)
        matched = kg_modules[idx]
        return find_module.invoke({"namespace": matched})

    # --- Fuzzy match ---
    match = process.extractOne(target_lower, kg_lower)
    if match:
        best_lower, score, _ = match
        best_original = kg_modules[kg_lower.index(best_lower)]

        if score >= 80:
            print(f"[KG] Fuzzy module match '{target}' → '{best_original}'")
            return find_module.invoke({"namespace": best_original})

        suggestion = best_original
    else:
        suggestion = None

    # --- No match in KG ---
    return {
        "found": False,
        "error": f"Module '{target}' is not defined in the ontology.",
        "valid_modules": kg_modules,
        "did_you_mean": suggestion
    }


# ── MRKL wrapper for trigger_order ────────────────────────────────────────
# ──────────────── trigger_order_wrap (handles *all* cases) ────────────────
import ast, json, re

def trigger_order_wrap(arg: Any) -> dict:
    """
    Strict KG-gated wrapper around trigger_order.

    Enforces BEFORE dispatch:
    ✔ start module exists
    ✔ goal module exists
    ✔ start → goal reachable in KG
    ✔ box exists (by id or color)

    If ANY condition fails → NO MQTT is published.
    """

    def parse_input(arg: Any) -> dict:
        if isinstance(arg, dict):
            return arg
        if isinstance(arg, str):
            try:
                return json.loads(arg)
            except json.JSONDecodeError:
                return _parse_kv(arg)
        raise ValueError("Unsupported input format")

    try:
        args = parse_input(arg)

        # ============================================================
        # 1) START / GOAL existence
        # ============================================================

        if "start" not in args:
            return {"found": False, "error": "Missing required field: start"}
        if "goal" not in args:
            return {"found": False, "error": "Missing required field: goal"}

        start_info = find_module_wrap(args["start"])
        goal_info  = find_module_wrap(args["goal"])

        if not start_info.get("found"):
            return {
                "found": False,
                "error": f"Invalid start module: {start_info.get('error')}"
            }

        if not goal_info.get("found"):
            return {
                "found": False,
                "error": f"Invalid goal module: {goal_info.get('error')}"
            }

        start_ns = start_info["namespace"]
        goal_ns  = goal_info["namespace"]

        # ============================================================
        # 2) KG PATH FEASIBILITY (HARD BLOCK)
        # ============================================================

        if not kg.reachable(start_ns, goal_ns):
            return {
                "found": False,
                "error": (
                    f"No valid transport path exists in the knowledge graph "
                    f"from '{start_ns}' to '{goal_ns}'."
                ),
                "start": start_ns,
                "goal": goal_ns,
                "reachable_neighbors_of_start": kg.neighbors(start_ns)
            }

        # ============================================================
        # 3) BOX VALIDATION (MANDATORY)
        # ============================================================

        box_info = None

        if "box_id" in args:
            box_info = find_box_wrap({"box_id": args["box_id"]})
            if not box_info.get("found"):
                return {
                    "found": False,
                    "error": f"Invalid box_id: {box_info.get('error')}",
                    "valid_box_ids": box_info.get("valid_box_ids"),
                    "did_you_mean": box_info.get("did_you_mean")
                }

        elif "box_color" in args:
            box_info = find_box_by_color_wrap({"color": args["box_color"]})
            if not box_info.get("found"):
                return {
                    "found": False,
                    "error": f"Invalid box_color: {box_info.get('error')}",
                    "valid_colors": box_info.get("valid_colors"),
                    "did_you_mean": box_info.get("did_you_mean")
                }

        else:
            return {
                "found": False,
                "error": "A transport order requires a box_id or box_color."
            }

        # Inject resolved box data
        if "id" in box_info:
            args["box_id"]    = box_info["id"]
            args["box_color"] = box_info["color"]
            args["box_pose"]  = box_info.get("global_pose")

        elif "boxes" in box_info and box_info["boxes"]:
            b = box_info["boxes"][0]
            args["box_id"]    = b["id"]
            args["box_color"] = b["color"]
            args["box_pose"]  = b.get("global_pose")

        # ============================================================
        # 4) FINAL SAFE PAYLOAD
        # ============================================================

        final_args = {
            "start":       start_ns,
            "goal":        goal_ns,
            "start_pose":  start_info["pose"],
            "goal_pose":   goal_info["pose"],
            "box_id":      args["box_id"],
            "box_color":   args["box_color"],
            "box_pose":    args.get("box_pose"),
        }

        # ============================================================
        # 5) DISPATCH (SAFE)
        # ============================================================

        return trigger_order.invoke(final_args)

    except Exception as e:
        return {
            "found": False,
            "error": f"trigger_order_wrap failed: {e}"
        }


# tools without args
list_boxes_wrap        = lambda _="": list_boxes.invoke({})
find_last_order_wrap   = lambda _="": find_last_order.invoke({})
confirm_last_order_wrap= lambda _="": confirm_last_order.invoke({})
diagnose_failure_wrap   = lambda _="": diagnose_failure.invoke({})
list_orders_wrap        = lambda _="": list_orders.invoke({})

# ---------- MRKL-compatible toolkit -------------------------
MRKL_TOOLS = [
    Tool("find_box",           find_box_wrap,
         "find_box(box_id:int)  → pose & attributes"),
    Tool("find_box_by_color",  find_box_by_color_wrap,
         "find_box_by_color(color:str)"),
    Tool("find_module",        find_module_wrap,
         "find_module(namespace:str)"),
    Tool("list_boxes",         list_boxes_wrap,
         "list_boxes() → summary of boxes"),
    Tool("find_last_order",    find_last_order_wrap,
         "find_last_order() → last completed order"),
    Tool("trigger_order", trigger_order_wrap,
    (
        "trigger_order(start|start_pose, goal|goal_pose "
        "[, box_id:int, box_color:str, box_pose:dict]) "
        "→ dispatch the order **and wait for the master’s response**.\n"
        "Examples:\n"
        "  • trigger_order(start=container_02, goal=container_01, box_color=blue)\n"
    )),
    Tool("confirm_last_order", confirm_last_order_wrap,
         "confirm_last_order() → success / failed"),
    Tool("diagnose_failure", diagnose_failure_wrap,
     "diagnose_failure() → reason for last known failure"),
    Tool("list_modules", lambda _="": list_modules.invoke({}),
         "list_modules() → all available module namespaces"),
    Tool("master_status", lambda _="": master_status.invoke({}),
         "master_status() → is the master online?"),
    Tool("list_orders", list_orders_wrap,
         "list_orders() → every cached order result (newest first)"),
    Tool("plan_path", plan_path_wrap,
     "plan_path(start:str, goal:str) → List of module steps to follow"),
    Tool(
        "find_closest_module",
        find_closest_module_wrap,
        "find_closest_module(x:float, y:float) → nearest module namespace"
    )
]

# -----------------------------------------------------------------------------
# END OF FILE
# -----------------------------------------------------------------------------

