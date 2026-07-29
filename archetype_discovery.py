"""
edf_randomizer_tool / archetype_discovery.py
------------------------------------------------------------
Refactored from the standalone discover_archetypes.py script (proven
against real weapon data across this whole project) into pure,
importable functions the orchestrator can call directly — no file I/O
required in the core logic, so it's fully unit-testable and the
orchestrator controls exactly when/where data gets written to disk.

The clustering logic itself (fingerprint fields, matching, grouping)
is UNCHANGED from the validated version — only the file-handling
wrapper around it is new.
"""

import re
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Any

# Fields that meaningfully describe HOW a weapon fires (not just its
# power level). Two weapons with identical values across all of these
# behave the same way mechanically, even if their numbers differ.
# UNCHANGED from the validated discover_archetypes.py.
FINGERPRINT_FIELDS = [
    "xgs_scene_object_class",
    "FireType",
    "FireSpreadType",
    "FireCount",
    "FireBurstCount",
    "SecondaryFire_Type",
    "LockonType",
    "AmmoClass",
    "AmmoIsPenetration",
]


@dataclass
class DiscoveryResult:
    summary: Dict[str, Any]
    total_weapons_processed: int
    total_categories: int
    missing_file_count: int


def get_var(sgo_data: dict, name: str):
    for v in sgo_data.get("variables", []):
        if v["name"] == name:
            return v
    return None


def get_scalar(sgo_data: dict, name: str):
    """Get a field's value whether it's a plain scalar or a ptr curve
    (in which case we want just the first/base value)."""
    var = get_var(sgo_data, name)
    if var is None:
        return None
    if var.get("type") == "ptr":
        if var["value"] and var["value"][0].get("type") in ("double", "string"):
            return var["value"][0]["value"]
        return None
    return var.get("value")


def sgo_path_to_json_filename(sgo_path: str) -> str:
    # e.g. "app:/weapon/AssultRifle01.sgo" -> "AssultRifle01.json"
    base = sgo_path.rsplit("/", 1)[-1]
    base = re.sub(r"\.sgo$", "", base, flags=re.IGNORECASE)
    return base + ".json"


def discover_archetypes(
    table_entries: List[dict],
    weapon_json_loader,
) -> DiscoveryResult:
    """
    Core clustering logic, decoupled from file I/O.

    table_entries: the raw list from WEAPONTABLE's
        variables[0]["value"] — same structure used throughout this
        whole project.
    weapon_json_loader: a callable taking a filename (str) and
        returning either the parsed sgo JSON dict, or None if that
        file doesn't exist / fails to parse. This indirection is what
        makes the function testable without real files on disk, and
        lets the orchestrator swap in a cached-baseline-aware loader
        later without touching this logic at all.
    """
    categories = defaultdict(lambda: defaultdict(list))
    missing = 0

    for entry in table_entries:
        internal_name = entry["value"][0]["value"]
        sgo_path = entry["value"][1]["value"]
        category_id = entry["value"][2]["value"]

        json_filename = sgo_path_to_json_filename(sgo_path)
        sgo_data = weapon_json_loader(json_filename)
        if sgo_data is None:
            missing += 1
            continue

        fingerprint = tuple(get_scalar(sgo_data, f) for f in FINGERPRINT_FIELDS)
        categories[category_id][fingerprint].append({
            "internal_name": internal_name,
            "sgo_path": sgo_path,
        })

    summary = {}
    for category_id, fp_groups in categories.items():
        clusters = []
        for fingerprint, members in fp_groups.items():
            clusters.append({
                "fingerprint": dict(zip(FINGERPRINT_FIELDS, fingerprint)),
                "representative": members[0],
                "member_count": len(members),
                "example_members": [m["internal_name"] for m in members[:5]],
            })
        clusters.sort(key=lambda c: -c["member_count"])
        summary[str(category_id)] = {
            "total_weapons_in_category": sum(c["member_count"] for c in clusters),
            "distinct_archetypes_found": len(clusters),
            "clusters": clusters,
        }

    total_weapons = sum(v["total_weapons_in_category"] for v in summary.values())
    return DiscoveryResult(
        summary=summary,
        total_weapons_processed=total_weapons,
        total_categories=len(summary),
        missing_file_count=missing,
    )


def make_directory_loader(weapon_json_dir: Path):
    """Convenience: build a weapon_json_loader that reads real files
    from a real folder — this is what the orchestrator will actually
    use in production, wired up via the same indirection tested below."""
    import json

    def loader(filename: str):
        path = weapon_json_dir / filename
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    return loader
