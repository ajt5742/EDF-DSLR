"""
edf_randomizer_tool / randomizer_core.py
------------------------------------------------------------
Refactored from the standalone full_roster_randomize.py script
(proven against real weapon data, including the tier-system rework
and the plain-double vs. ptr-curve field fix) into pure, importable
functions — same weapon_json_loader indirection pattern as
archetype_discovery.py, for the same reason: fully testable without
real files, orchestrator controls when/where results get written.

ALL tuning values (tier ranges, weights, affected stat fields,
excluded categories) are UNCHANGED from the validated version.
"""

import re
import copy
import random
from dataclasses import dataclass, field
from typing import Dict, List, Any, Callable, Optional

from settings import DEFAULT_SETTINGS

# Categories confirmed enemy/vehicle/placeholder — NOT user-configurable,
# since getting this wrong risks corrupting non-weapon game data, not
# just changing preference. Unchanged from the validated version.
KNOWN_EXCLUDED_CATEGORIES = {"7", "10", "8", "9", "120", "200", "206",
                             "207", "208", "209", "307", "308", "309", "320"}


@dataclass
class RandomizeResult:
    output_sgo_by_filename: Dict[str, dict]  # filename -> modified sgo JSON
    output_text_entries: List[dict]
    seed_used: int
    stats: Dict[str, Any] = field(default_factory=dict)


def is_enemy_or_placeholder_category(cat_info: dict) -> bool:
    reps = [cl["representative"]["internal_name"] for cl in cat_info.get("clusters", [])]
    if not reps:
        return True
    e_prefixed = sum(1 for r in reps if r[:1] in ("e", "E"))
    placeholder = sum(1 for r in reps if "None" in r)
    return (e_prefixed / len(reps)) >= 0.8 or (placeholder / len(reps)) >= 0.8


def roll_tier_and_multiplier(rng: random.Random, tiers: List[dict]):
    weights = [t["weight"] for t in tiers]
    chosen = rng.choices(tiers, weights=weights, k=1)[0]
    mult = round(rng.uniform(chosen["multiplier_min"], chosen["multiplier_max"]), 4)
    return chosen["name"], chosen["tag"], mult


def get_var(sgo_data: dict, name: str):
    for v in sgo_data["variables"]:
        if v["name"] == name:
            return v
    return None


def get_scalar(sgo_data: dict, name: str):
    """Get a field's value whether it's a plain scalar or a ptr curve
    (in which case we want just the first/base value). Same logic as
    archetype_discovery.py's version, duplicated here to avoid an
    unnecessary cross-module coupling for one small helper."""
    var = get_var(sgo_data, name)
    if var is None:
        return None
    if var.get("type") == "ptr":
        if var["value"] and var["value"][0].get("type") in ("double", "string"):
            return var["value"][0]["value"]
        return None
    return var.get("value")


def sgo_path_to_filename(sgo_path: str) -> str:
    base = sgo_path.rsplit("/", 1)[-1]
    return re.sub(r"\.sgo$", "", base, flags=re.IGNORECASE) + ".json"


def _extract_scalar_if_possible(donor_var: dict):
    """If donor_var is already a plain scalar, return its value as-is.
    If it's a "ptr" curve whose first element is a numeric scalar
    (the base value of the curve, matching the convention used
    throughout this game's data for tier-scalable stats), extract
    that base value instead of the whole curve structure."""
    if donor_var.get("type") != "ptr":
        return donor_var.get("value")
    values = donor_var.get("value") or []
    if values and isinstance(values[0], dict) and values[0].get("type") == "double":
        return values[0]["value"]
    return None


def apply_behavior_transplant(clone_sgo: dict, donor_sgo: dict, behavior_fields: List[str]) -> bool:
    donor_vars_by_name = {v["name"]: v for v in donor_sgo["variables"]}
    changed = False
    for i, var in enumerate(clone_sgo["variables"]):
        if var["name"] in behavior_fields and var["name"] in donor_vars_by_name:
            donor_var = donor_vars_by_name[var["name"]]
            if var.get("type") == donor_var.get("type"):
                clone_sgo["variables"][i] = copy.deepcopy(donor_var)
                changed = True
                continue
            # Types differ. Found via a second real crash: a donor's
            # AmmoExplosion was a "ptr" curve while the recipient's own
            # was a plain "double" — our original type-safety fix
            # correctly avoided structural corruption by skipping it,
            # but that left AmmoClass="BombBullet02" (a real explosive)
            # paired with AmmoExplosion=0 (a scalar meaning "never
            # explodes") — the exact same logical contradiction as the
            # FIRST crash, just caused by our own safety mechanism this
            # time. Rather than only "same type or skip", extract a
            # real usable scalar from a donor curve when the recipient
            # needs one — preserves the actual meaningful value while
            # staying fully safe (the recipient still gets a valid
            # plain double, exactly what its own code expects there).
            if var.get("type") == "double" and donor_var.get("type") == "ptr":
                extracted = _extract_scalar_if_possible(donor_var)
                if extracted is not None:
                    clone_sgo["variables"][i] = {
                        "name": var["name"], "type": "double", "value": extracted
                    }
                    changed = True
                continue
            # Any other type mismatch (e.g. string vs ptr) has no safe
            # extraction — skip as before, leaving the original value.
            continue
    return changed


def apply_power_roll(sgo_data: dict, mult: float, higher_is_better: List[str],
                       lower_is_better: List[str]) -> bool:
    dampened_inv = round(mult ** -0.5, 4)
    dampened_inv = max(0.35, dampened_inv)
    touched = False

    def scale_field(field_name, factor):
        nonlocal touched
        var = get_var(sgo_data, field_name)
        if not var:
            return
        if var.get("type") == "ptr" and var["value"]:
            base = var["value"][0]
            if base.get("type") == "double":
                base["value"] = round(base["value"] * factor, 4)
                touched = True
        elif var.get("type") == "double":
            if var["value"] != 0:
                var["value"] = round(var["value"] * factor, 4)
                touched = True

    for f in higher_is_better:
        scale_field(f, mult)
    for f in lower_is_better:
        scale_field(f, dampened_inv)
    return touched


def get_stat_value(sgo_data: dict, field_name: str):
    var = get_var(sgo_data, field_name)
    if var and var.get("type") == "ptr" and var["value"]:
        return var["value"][0]["value"]
    return None


def build_text_entry(base_text_entry: dict, sgo_data: dict, tag: str) -> dict:
    text_clone = copy.deepcopy(base_text_entry)
    if tag:
        text_clone["value"][0]["value"] = f"{tag}{text_clone['value'][0]['value']}"

    field_by_label = {
        "Capacity": "AmmoCount", "Damage": "AmmoDamage",
        "Reload Time": "ReloadTime", "Shot Speed": "AmmoSpeed",
        "Accuracy": "FireAccuracy",
    }
    stat_list = text_clone["value"][2]["value"]
    for stat_entry in stat_list:
        parts = stat_entry["value"]
        if len(parts) < 3:
            continue
        label = parts[0]["value"]
        sgo_field = field_by_label.get(label)
        if not sgo_field:
            continue
        new_val = get_stat_value(sgo_data, sgo_field)
        if new_val is None:
            continue
        for curve in parts[2:]:
            if curve.get("type") == "ptr" and curve["value"]:
                base_val = curve["value"][0]
                if base_val.get("type") == "double":
                    base_val["value"] = round(new_val, 4)
    return text_clone


def randomize_full_roster(
    table_entries: List[dict],
    text_entries: List[dict],
    archetype_data: dict,
    weapon_json_loader: Callable[[str], Optional[dict]],
    seed: Optional[int] = None,
    settings: Optional[dict] = None,
) -> RandomizeResult:
    """
    Core randomization logic, decoupled from file I/O.

    weapon_json_loader: same indirection as archetype_discovery.py —
    a callable taking a filename and returning parsed sgo JSON, or
    None if unavailable. Lets this be tested without real files, and
    lets the orchestrator wire in a baseline-cache-aware loader later.

    seed: if None, a fresh OS-random seed is generated here (not left
    to the caller), and returned in the result so it can be shown to
    the user / saved alongside the output.

    settings: tier ranges/weights, swappable behavior fields, and
    scalable stats — all genuine preference, user-editable via
    settings.json (see settings.py). Defaults to DEFAULT_SETTINGS,
    matching exactly what's been validated through real play, so
    every existing caller that doesn't pass this gets identical
    behavior to before this became configurable. The throw-safety
    restriction below is NOT part of settings — it stays hardcoded,
    since it exists specifically because of a real, confirmed crash.
    """
    if seed is None:
        import os
        seed = int.from_bytes(os.urandom(4), byteorder="big")
    rng = random.Random(seed)

    if settings is None:
        settings = DEFAULT_SETTINGS
    tiers = settings["tiers"]
    behavior_fields = settings["swappable_behavior_fields"]
    higher_is_better = settings["stats_higher_is_better"]

    # Cache for the Engineer-sound safety check below — avoids
    # reloading and re-checking the same cluster representative's
    # file repeatedly for every recipient weapon that shares a
    # category with it.
    _engineer_sound_cache: dict = {}

    def _is_engineer_sound_donor(cluster: dict) -> bool:
        sgo_path = cluster["representative"]["sgo_path"]
        if sgo_path in _engineer_sound_cache:
            return _engineer_sound_cache[sgo_path]
        donor_filename = sgo_path_to_filename(sgo_path)
        donor_data = weapon_json_loader(donor_filename)
        result = False
        if donor_data:
            fire_se = get_var(donor_data, "FireSe")
            if fire_se and fire_se.get("type") == "ptr":
                for item in fire_se.get("value", []):
                    if isinstance(item, dict) and item.get("type") == "string":
                        if "Engineer" in str(item.get("value") or ""):
                            result = True
                        break
        _engineer_sound_cache[sgo_path] = result
        return result
    lower_is_better = settings["stats_lower_is_better"]

    output_sgo_by_filename: Dict[str, dict] = {}
    new_text_entries: List[dict] = []

    stats = {"processed": 0, "skipped_excluded": 0, "skipped_no_file": 0,
             "functional_change": 0, "power_only": 0, "power_mults": [],
             "tier_counts": {t["name"]: 0 for t in tiers}}

    for idx, entry in enumerate(table_entries):
        internal_name = entry["value"][0]["value"]
        sgo_path = entry["value"][1]["value"]
        category_id = str(entry["value"][2]["value"])

        if category_id in KNOWN_EXCLUDED_CATEGORIES:
            stats["skipped_excluded"] += 1
            new_text_entries.append(text_entries[idx])
            continue

        cat_info = archetype_data.get(category_id)
        if cat_info and is_enemy_or_placeholder_category(cat_info):
            stats["skipped_excluded"] += 1
            new_text_entries.append(text_entries[idx])
            continue

        own_filename = sgo_path_to_filename(sgo_path)
        own_sgo = weapon_json_loader(own_filename)
        if own_sgo is None:
            stats["skipped_no_file"] += 1
            new_text_entries.append(text_entries[idx])
            continue

        donor_sgo = None
        if cat_info and cat_info["clusters"]:
            # Confirmed via real gameplay crash: a rocket launcher
            # inherited a hand grenade's full behavior (same UI
            # category, different engine class) while keeping its own
            # launcher model/animation — throw-style aim/release logic
            # landing on a weapon whose animation rig was never built
            # for it, corrupting state badly enough to crash.
            #
            # Deliberately narrow fix, not a full same-class-only
            # restriction: only block THROW-type donors from being
            # transplanted onto non-throw weapons. This is a real,
            # confirmed-bad case we're blocking; every other cross-class
            # pairing within a category remains untested but is being
            # kept poolable to preserve variety, an explicit tradeoff
            # rather than an oversight. Thrown weapons can still donate
            # to EACH OTHER, so grenade-side variety isn't lost either —
            # only this one specific direction is blocked.
            own_class = get_scalar(own_sgo, "xgs_scene_object_class") or ""
            # Engineer-sound safety rule, added after a real, confirmed
            # crash traced to a specific mechanic, not donor origin
            # generally (an earlier enemy-donor-blanket exclusion was
            # tried and reverted, since real gameplay showed most
            # enemy-donor transplants work fine). The actual signal:
            # SecondaryFire_Type=3 is used by TWO distinct, unrelated
            # weapon families sharing that raw value — Ranger's own
            # legitimate thrown-bomb series (FireSe: "weapon_Ranger_GL_
            # ...", confirmed safe, real player mechanic) and the
            # "Limpet" remote-detonation family (FireSe: "weapon_
            # Engineer_BOM_...", confirmed to crash on fire). The
            # SecondaryFire_Type value alone can't distinguish these —
            # only the FireSe sound tag can. Blocks the "Engineer"
            # family from donating to non-"Engineer" recipients, while
            # leaving Ranger's own bomb variety, and everything else,
            # completely unrestricted.
            own_fire_se = get_var(own_sgo, "FireSe")
            own_is_engineer_sound = False
            if own_fire_se and own_fire_se.get("type") == "ptr":
                for item in own_fire_se.get("value", []):
                    if isinstance(item, dict) and item.get("type") == "string":
                        if "Engineer" in str(item.get("value") or ""):
                            own_is_engineer_sound = True
                        break
            compatible_clusters = [
                c for c in cat_info["clusters"]
                if ("Throw" not in str(c["fingerprint"].get("xgs_scene_object_class") or "")
                    or "Throw" in own_class)
                and (own_is_engineer_sound or not _is_engineer_sound_donor(c))
            ]
            if compatible_clusters:
                chosen_cluster = rng.choice(compatible_clusters)
                donor_name = chosen_cluster["representative"]["internal_name"]
                donor_sgo_path = chosen_cluster["representative"]["sgo_path"]
                if donor_name != internal_name:
                    donor_filename = sgo_path_to_filename(donor_sgo_path)
                    donor_sgo = weapon_json_loader(donor_filename)

        variant_sgo = copy.deepcopy(own_sgo)
        functional_change = False
        if donor_sgo:
            functional_change = apply_behavior_transplant(variant_sgo, donor_sgo, behavior_fields)

        tier_name, tag, power_mult = roll_tier_and_multiplier(rng, tiers)
        power_touched = apply_power_roll(variant_sgo, power_mult, higher_is_better, lower_is_better)
        stats["power_mults"].append(power_mult)
        stats["tier_counts"][tier_name] += 1
        if functional_change:
            stats["functional_change"] += 1
        elif power_touched:
            stats["power_only"] += 1

        output_sgo_by_filename[own_filename] = variant_sgo
        new_text_entries.append(build_text_entry(text_entries[idx], variant_sgo, tag))
        stats["processed"] += 1

    return RandomizeResult(
        output_sgo_by_filename=output_sgo_by_filename,
        output_text_entries=new_text_entries,
        seed_used=seed,
        stats=stats,
    )
