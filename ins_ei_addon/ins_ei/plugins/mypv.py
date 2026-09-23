"""my-PV plugin profiles for AC-THOR / AC ELWA families."""
from __future__ import annotations

PROFILES = {
    "generic": {"aliases": {}, "capabilities": set()},
    "ac_thor": {"aliases": {}, "capabilities": {"power_to_heat"}},
    "ac_thor_9s": {"aliases": {}, "capabilities": {"power_to_heat", "three_phase", "0_9kw"}},
    "ac_elwa_2": {"aliases": {}, "capabilities": {"power_to_heat"}},
}

CANONICAL_TARGETS = {
    "electrical_power": ("power_to_heat", "electrical_power"),
    "temperature": ("power_to_heat", "temperature"),
    "target_temperature": ("power_to_heat", "target_temperature"),
}

def normalize(raw: dict, profile: str = "generic") -> tuple[list[dict], dict]:
    cfg = PROFILES.get(profile, PROFILES["generic"])
    aliases = cfg.get("aliases", {})
    points, unmapped = [], {}
    for raw_key, value in raw.items():
        semantic = aliases.get(raw_key, raw_key)
        target = CANONICAL_TARGETS.get(semantic)
        if target is None:
            unmapped[raw_key] = value
            continue
        points.append({"component_id": target[0], "point": target[1], "value": value, "source": raw_key})
    return points, unmapped
