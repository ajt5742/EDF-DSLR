"""
edf_randomizer_tool / settings.py
------------------------------------------------------------
User-editable tuning, externalized to a JSON file sitting next to the
exe — so anyone can adjust tier ranges, weights, which fields swap
between weapons, and which stats scale, without needing to touch code
or rebuild anything.

DELIBERATELY NOT included here: the throw-vs-non-throw behavior
restriction in randomizer_core.py. That rule exists because of a real,
confirmed crash during testing — it stays hardcoded as a safety floor,
not exposed as a togglable preference. Everything else in this file is
genuine preference, safe to open up fully.

DEFAULT_SETTINGS mirrors exactly what's been validated through actual
play tonight — anyone who never touches the JSON file gets identical
behavior to everything already tested.
"""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple, Optional


DEFAULT_SETTINGS = {
    "tiers": [
        {"name": "Common", "tag": "", "multiplier_min": 0.90, "multiplier_max": 1.10, "weight": 25},
        {"name": "Uncommon", "tag": "[Uncommon] ", "multiplier_min": 1.10, "multiplier_max": 1.25, "weight": 30},
        {"name": "Rare", "tag": "[Rare] ", "multiplier_min": 1.25, "multiplier_max": 1.50, "weight": 25},
        {"name": "Epic", "tag": "[Epic] ", "multiplier_min": 1.50, "multiplier_max": 1.80, "weight": 15},
        {"name": "Legendary", "tag": "[Legendary] ", "multiplier_min": 1.80, "multiplier_max": 2.20, "weight": 5},
    ],
    "swappable_behavior_fields": [
        "FireType", "FireSpreadType", "FireSpreadWidth", "FireCount",
        "FireBurstCount", "FireBurstInterval", "SecondaryFire_Type",
        "SecondaryFire_Parameter", "AmmoClass", "AmmoIsPenetration",
        "LockonType", "LockonTargetType", "Lockon_DistributionType",
        "LockonRange", "LockonAngle", "LockonTime",
        # Added after real crash investigation: AmmoClass alone was
        # transplanting without its effect-resource dependencies,
        # leaving a weapon with an ammo class whose visual/particle
        # effects were never actually copied — e.g. AmmoClass="EfsBullet"
        # paired with a donor's specific effect archive reference in
        # "resource"/"Ammo_CustomParameter" that stayed as the
        # RECIPIENT's own, unrelated original values (a shell-casing
        # reference in the confirmed case). These now travel together
        # as one consistent bundle whenever a transplant happens.
        "resource", "Ammo_CustomParameter",
    ],
    "stats_higher_is_better": ["AmmoDamage", "AmmoCount", "AmmoSpeed", "AmmoExplosion"],
    "stats_lower_is_better": ["ReloadTime", "FireInterval", "FireAccuracy", "FireRecoil"],
}

SETTINGS_FILENAME = "settings.json"


def get_settings_path(app_root: Path) -> Path:
    return app_root / SETTINGS_FILENAME


def validate_settings(settings: dict) -> Tuple[bool, str]:
    """Checks structural sanity — not perfection. Minor issues (weights
    not summing to exactly 100) get normalized rather than rejected;
    only genuinely broken structure fails outright, so a small typo
    doesn't lock someone out of using the tool at all."""
    if not isinstance(settings, dict):
        return False, "Settings file must contain a JSON object."

    tiers = settings.get("tiers")
    if not isinstance(tiers, list) or len(tiers) == 0:
        return False, "'tiers' must be a non-empty list."

    for i, tier in enumerate(tiers):
        if not isinstance(tier, dict):
            return False, f"Tier #{i+1} must be an object."
        for field in ("name", "tag"):
            if not isinstance(tier.get(field), str):
                return False, f"Tier #{i+1}: '{field}' must be text."
        for field in ("multiplier_min", "multiplier_max", "weight"):
            val = tier.get(field)
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                return False, f"Tier #{i+1}: '{field}' must be a number."
            if val < 0:
                return False, f"Tier #{i+1}: '{field}' can't be negative."
        if tier["multiplier_min"] > tier["multiplier_max"]:
            return False, (f"Tier #{i+1} ('{tier['name']}'): multiplier_min "
                            f"can't be greater than multiplier_max.")

    for list_field in ("swappable_behavior_fields", "stats_higher_is_better",
                        "stats_lower_is_better"):
        val = settings.get(list_field)
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            return False, f"'{list_field}' must be a list of text field names."

    overlap = (set(settings["stats_higher_is_better"]) &
               set(settings["stats_lower_is_better"]))
    if overlap:
        return False, (f"These fields appear in both 'stats_higher_is_better' "
                        f"and 'stats_lower_is_better', which is contradictory: "
                        f"{', '.join(sorted(overlap))}")

    return True, ""


def _normalize_weights(tiers: List[dict]) -> List[dict]:
    """Weights don't need to sum to exactly 100 — normalized
    proportionally either way, so '1, 1, 1, 1, 1' works just as
    correctly as '20, 20, 20, 20, 20' for equal odds."""
    total = sum(t["weight"] for t in tiers)
    if total <= 0:
        return tiers
    for t in tiers:
        t["weight"] = t["weight"] / total * 100
    return tiers


def load_settings(app_root: Path) -> Tuple[dict, Optional[str]]:
    """
    Loads settings.json from next to the exe, creating it with
    defaults on first run if it doesn't exist yet. Returns
    (settings_dict, warning_message_or_None) — a warning (not an
    exception) is returned for a genuinely invalid file, alongside
    falling back to defaults, so a typo in hand-edited JSON doesn't
    prevent the tool from running at all; the caller decides whether
    and how to surface that warning.
    """
    path = get_settings_path(app_root)

    if not path.exists():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)
        return DEFAULT_SETTINGS, None

    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return DEFAULT_SETTINGS, (f"Couldn't read settings.json ({e}) — "
                                    f"using default settings instead.")

    is_valid, error = validate_settings(loaded)
    if not is_valid:
        return DEFAULT_SETTINGS, (f"settings.json has a problem: {error} "
                                    f"— using default settings instead.")

    loaded["tiers"] = _normalize_weights(loaded["tiers"])
    return loaded, None
