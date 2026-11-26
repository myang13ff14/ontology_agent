"""Synthetic operational data for the laptop-support sample: service cases,
diagnostic executions and work orders.

ALL RECORDS ARE SYNTHETIC. Each case is simulated by walking the ontology's own
checklists, outcomes, LEADS_TO branches and decision rules, so the operational
data is consistent with the knowledge graph. Seeded, so runs are reproducible.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from ..db import Database
from ..ontology import rules
from ..ontology.store import OntologyStore

OPERATIONAL_TABLES = ["service_case", "diagnostic_execution", "work_order"]

# Relative frequency of each reportable symptom.
SYMPTOM_WEIGHTS = {
    "S_NOT_CHARGING": 12, "S_CHARGING_INTERMITTENT": 5, "S_PLUGGED_NOT_CHARGING": 4, "S_NO_POWER": 9,
    "S_UNEXPECTED_SHUTDOWN": 5, "S_BATTERY_DRAIN": 10, "S_BATTERY_SWOLLEN": 2, "S_BATTERY_NOT_DETECTED": 2,
    "S_BLACK_SCREEN": 9, "S_DISPLAY_FAILURE": 3, "S_DISPLAY_FLICKER": 5, "S_DISPLAY_LINES": 3,
    "S_KEYBOARD_FAILURE": 5, "S_KEYS_STUCK": 6, "S_KEYBOARD_BACKLIGHT": 2, "S_TOUCHPAD": 3,
    "S_WIFI_NOT_DETECTED": 4, "S_WIFI_DROPS": 8, "S_WIFI_SLOW": 5,
}
MODEL_WEIGHTS = {"DM_PRO14": 22, "DM_PRO16": 14, "DM_AIR13": 30, "DM_WORK15": 20, "DM_WORK14": 14}
SOFTWARE_FIXES = {"A_BIOS_UPDATE", "A_GPU_DRIVER_UPDATE", "A_KEYBOARD_DRIVER_REINSTALL", "A_WIFI_DRIVER_UPDATE",
                  "A_NETWORK_RESET", "A_REDUCE_POWER_USAGE", "A_CHECK_FILTER_KEYS", "A_REBOOT", "A_FORGET_REJOIN",
                  "A_TOGGLE_WIFI", "A_BATTERY_CALIBRATION", "A_BRIGHTNESS_CHECK"}
# Chance that a fix-type action resolves the case: (default, {faulty component: chance}).
FIX_CHANCE = {
    "A_TRY_OTHER_OUTLET": (0.08, {}), "A_POWER_RESET": (0.2, {"C_FIRMWARE": 0.6, "C_MOTHERBOARD": 0.12}),
    "A_BIOS_UPDATE": (0.1, {"C_FIRMWARE": 0.75}), "A_RESEAT_BATTERY": (0.1, {"C_BATTERY": 0.4}),
    "A_RESEAT_RAM": (0.05, {"C_RAM": 0.85}), "A_REDUCE_POWER_USAGE": (0.1, {"C_OS_SOFTWARE": 0.75}),
    "A_BATTERY_CALIBRATION": (0.15, {"C_FIRMWARE": 0.4}), "A_BRIGHTNESS_CHECK": (0.07, {}),
    "A_GPU_DRIVER_UPDATE": (0.1, {"C_OS_SOFTWARE": 0.8}), "A_REBOOT": (0.12, {"C_OS_SOFTWARE": 0.35}),
    "A_CHECK_FILTER_KEYS": (0.04, {"C_OS_SOFTWARE": 0.3}), "A_KEYBOARD_DRIVER_REINSTALL": (0.1, {"C_OS_SOFTWARE": 0.7}),
    "A_CLEAN_KEYBOARD": (0.1, {"C_KEYBOARD": 0.4}), "A_TOGGLE_WIFI": (0.12, {"C_OS_SOFTWARE": 0.3}),
    "A_RESTART_ROUTER": (0.7, {}), "A_FORGET_REJOIN": (0.3, {"C_OS_SOFTWARE": 0.45}),
    "A_WIFI_DRIVER_UPDATE": (0.15, {"C_OS_SOFTWARE": 0.7}), "A_NETWORK_RESET": (0.2, {"C_OS_SOFTWARE": 0.8}),
}
# Chance of the listed outcome code for check-type actions: (default, {faulty component: chance}).
CHECK_CHANCE = {
    ("A_CHECK_POWER_LED", "LED_OFF"): (0.45, {}),
    ("A_VERIFY_ADAPTER", "ADAPTER_FAULTY"): (0.03, {"C_AC_ADAPTER": 0.6}),
    ("A_INSPECT_PORT", "PORT_DAMAGED"): (0.02, {"C_CHARGING_PORT": 0.7}),
    ("A_TRY_KNOWN_GOOD_ADAPTER", "CHARGES"): (0.04, {"C_AC_ADAPTER": 0.9}),
    ("A_CHECK_BATTERY_HEALTH", "DEGRADED"): (0.05, {"C_BATTERY": 0.8}),
    ("A_EXTERNAL_MONITOR_TEST", "EXT_SAME_ISSUE"): (0.05, {"C_GPU": 0.8, "C_RAM": 0.8, "C_MOTHERBOARD": 0.8, "C_OS_SOFTWARE": 0.6}),
    ("A_DISPLAY_SELF_TEST", "FAILS"): (0.08, {"C_DISPLAY_PANEL": 0.9, "C_DISPLAY_CABLE": 0.8}),
    ("A_EXTERNAL_KEYBOARD_TEST", "EXT_KB_FAILS"): (0.03, {"C_OS_SOFTWARE": 0.7}),
    ("A_BIOS_KEY_TEST", "KEYS_FAIL"): (0.1, {"C_KEYBOARD": 0.9, "C_KEYBOARD_CABLE": 0.9}),
    ("A_OTHER_DEVICE_TEST", "OTHERS_FAIL"): (0.35, {}),
    ("A_WIFI_DIAG", "FAILS"): (0.1, {"C_WIFI_CARD": 0.85, "C_ANTENNA": 0.7}),
    ("A_DISPATCH_REPAIR", "DECLINED"): (0.08, {}),
}
MAX_STEPS = 14


def _weighted(rng: random.Random, weights: dict):
    keys = sorted(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


class _Graph:
    """The slices of the ontology the simulator needs, read once through the store."""

    def __init__(self, store: OntologyStore):
        self.nodes = {n["node_id"]: n for n in store.all_nodes()}
        self.out: dict[tuple[str, str], list[dict]] = {}
        for e in store.all_edges():
            self.out.setdefault((e["source_node_id"], e["relationship_type"]), []).append(e)
        for edges in self.out.values():
            edges.sort(key=lambda e: (e["properties"].get("step_order", 0), e["edge_id"]))

    def targets(self, node_id: str, relationship: str) -> list[dict]:
        return self.out.get((node_id, relationship), [])

    def inherited(self, symptom_id: str, relationship: str) -> list[dict]:
        cursor = symptom_id
        while cursor:
            found = self.targets(cursor, relationship)
            if found:
                return found
            parents = self.targets(cursor, "SUBTYPE_OF")
            cursor = parents[0]["target_node_id"] if parents else None
        return []


def generate(db: Database, store: OntologyStore, case_count: int = 2000, seed: int = 42) -> dict:
    rng = random.Random(seed)
    graph = _Graph(store)
    for table in OPERATIONAL_TABLES:
        db.execute(f"DELETE FROM {table}")

    cases, executions, work_orders = [], [], []
    start = datetime(2025, 1, 1, 8, 0)
    for index in range(1, case_count + 1):
        case_id = f"CASE-{index:05d}"
        symptom_id = _weighted(rng, SYMPTOM_WEIGHTS)
        symptom = graph.nodes[symptom_id]
        model_id = _weighted(rng, MODEL_WEIGHTS)
        model = graph.nodes[model_id]["properties"]
        opened = start + timedelta(minutes=rng.randrange(0, 365 * 24 * 60))
        indicated = graph.inherited(symptom_id, "INDICATES")
        component = (_weighted(rng, {e["target_node_id"]: e["properties"]["likelihood"] for e in indicated})
                     if indicated else None)
        component_props = graph.nodes[component]["properties"] if component else {}
        context = {
            "removable_battery": model["removable_battery"],
            "in_warranty": rng.random() < 0.72,
            "customer_accepts_paid_repair": rng.random() < 0.55,
            "customer_technical": rng.random() < 0.35,
            "has_admin_rights": rng.random() < 0.86,
            "ac_connected": True,
            "battery_percent": rng.randint(3, 100),
            "battery_swollen": symptom_id == "S_BATTERY_SWOLLEN",
            "device_powered_off": False,
            "part_is_customer_replaceable": bool(component_props.get("customer_replaceable"))
            or (component == "C_BATTERY" and model["removable_battery"]),
        }

        checklists = graph.inherited(symptom_id, "HAS_CHECKLIST")
        resolution, minutes, clock, step = "escalated", 4.0, opened, 0
        if checklists:
            checklist_id = checklists[0]["target_node_id"]
            context["checklist_id"] = checklist_id
            steps = [e["target_node_id"] for e in graph.targets(checklist_id, "CONTAINS")]
            action_id: str | None = steps[0]
            while action_id and step < MAX_STEPS:
                if not _eligible(graph, action_id, context):
                    # Not allowed for this customer/device: move on without performing it.
                    if action_id == "A_SHIP_PART":
                        action_id = "A_DISPATCH_REPAIR"
                    elif action_id in steps and steps.index(action_id) + 1 < len(steps):
                        action_id = steps[steps.index(action_id) + 1]
                    else:
                        action_id = "A_ESCALATE_L2" if action_id != "A_ESCALATE_L2" else None
                    continue
                step += 1
                action = graph.nodes[action_id]
                outcome = _pick_outcome(rng, graph, action_id, component)
                duration = round(action["properties"]["estimated_minutes"] * rng.uniform(0.7, 1.5), 1)
                minutes += duration
                clock += timedelta(minutes=duration)
                kind = outcome["properties"]["resolution"]
                executions.append({
                    "execution_id": f"EX-{len(executions) + 1:06d}", "case_id": case_id, "step_number": step,
                    "action_id": action_id, "outcome_id": outcome["node_id"], "succeeded": kind == "resolved",
                    "duration_minutes": duration, "executed_at": clock, "is_synthetic": True,
                })
                if kind == "resolved":
                    resolution = "fixed_on_phone"
                    break
                if kind == "work_order":
                    resolution = "work_order"
                    shipped = action_id == "A_SHIP_PART"
                    work_orders.append({
                        "work_order_id": f"WO-{len(work_orders) + 1:05d}", "case_id": case_id, "created_at": clock,
                        "work_order_type": "part_shipment" if shipped else "depot_repair",
                        "component_id": component,
                        "status": rng.choices(["completed", "in_progress", "cancelled"], [0.8, 0.15, 0.05])[0],
                        "cost_usd": round(rng.uniform(25, 90) if shipped else rng.uniform(120, 480), 2),
                        "is_synthetic": True,
                    })
                    break
                if kind == "escalate":
                    resolution = "escalated"
                    break
                action_id = _next_action(graph, outcome["node_id"], context)
            else:
                resolution = "escalated"
        phrasing = rng.choice([symptom["canonical_name"]] + symptom["synonyms"])
        cases.append({
            "case_id": case_id, "opened_at": opened, "closed_at": opened + timedelta(minutes=minutes),
            "channel": rng.choices(["phone", "chat", "email"], [0.6, 0.3, 0.1])[0],
            "device_model_id": model_id, "symptom_id": symptom_id,
            "call_driver_id": graph.targets(symptom_id, "HAS_CALL_DRIVER")[0]["target_node_id"],
            "reported_text": f"Customer reports: {phrasing.lower()}", "handle_time_minutes": round(minutes, 1),
            "resolution_type": resolution,
            "escalated": resolution == "escalated" or rng.random() < 0.04,
            "in_warranty": context["in_warranty"], "agent_id": f"AGENT-{rng.randint(1, 24):02d}",
            "is_synthetic": True,
        })

    db.insert_many("service_case", cases)
    db.insert_many("diagnostic_execution", executions)
    db.insert_many("work_order", work_orders)
    return {"service_cases": len(cases), "diagnostic_executions": len(executions), "work_orders": len(work_orders),
            "seed": seed}


def _eligible(graph: _Graph, action_id: str, context: dict) -> bool:
    for edge in graph.targets(action_id, "GOVERNED_BY"):
        expression = graph.nodes[edge["target_node_id"]]["properties"]["expression"]
        facts = dict(context)
        if "device_powered_off" in rules.referenced_names(expression):
            # Simulation assumption: the customer powers off and unplugs when asked to.
            facts.update(device_powered_off=True, ac_connected=False)
        if rules.evaluate(expression, facts)["satisfied"] is not True:
            return False
    return True


def _pick_outcome(rng: random.Random, graph: _Graph, action_id: str, component: str | None) -> dict:
    outcomes = [graph.nodes[e["target_node_id"]] for e in graph.targets(action_id, "HAS_OUTCOME")]
    by_code = {o["properties"]["code"]: o for o in outcomes}
    if len(outcomes) == 1:
        return outcomes[0]
    if "FIXED" in by_code:
        default, special = FIX_CHANCE.get(action_id, (0.15, {}))
        chance = special.get(component, default)
        if component and action_id in SOFTWARE_FIXES and component not in special:
            chance *= 0.4  # a software fix rarely cures a hardware fault
        return by_code["FIXED"] if rng.random() < chance else by_code["NO_CHANGE"]
    for code, outcome in by_code.items():
        if (action_id, code) in CHECK_CHANCE:
            default, special = CHECK_CHANCE[(action_id, code)]
            other = next(o for c, o in by_code.items() if c != code)
            return outcome if rng.random() < special.get(component, default) else other
    return rng.choice(outcomes)


def _next_action(graph: _Graph, outcome_id: str, context: dict) -> str | None:
    for edge in graph.targets(outcome_id, "LEADS_TO"):
        condition = edge["properties"].get("condition")
        if not condition or rules.evaluate(condition, context)["satisfied"] is True:
            return edge["target_node_id"]
    return "A_ESCALATE_L2"
