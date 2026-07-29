"""
edf_randomizer_tool / orchestrator.py
------------------------------------------------------------
Wires together config, baseline_cache, tool_wrappers,
archetype_discovery, and randomizer_core into one real run.

Key design point, carried over directly from tonight's real project
work: when a weapon-modifying mod (6.9x or similar) is detected, we
still need the VANILLA base weapon files too — many of the mod's own
table entries still point at original vanilla filenames that only
exist in Root.cpk, not in Mods/WEAPON/. So extraction always includes
vanilla, and layers the mod's own added files on top when present —
exactly the WEAPON_JSON_ALL merge approach validated earlier tonight.

All actual tool calls (CriPakTools, sgott) are dependency-injected via
tool_wrappers' functions, so this orchestration logic is testable with
fake stand-ins, same pattern used throughout this rebuild.
"""

import json
import shutil
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable, Any

from config import validate_install_path, InstallInfo, generate_random_seed, make_run_id, MARKER_FILENAME, read_own_marker, get_app_root
from settings import load_settings
from baseline_cache import cache_baseline, CacheResult
from tool_wrappers import run_cripaktools, convert_batch, ToolExecutionError
from archetype_discovery import discover_archetypes, make_directory_loader
from randomizer_core import randomize_full_roster, RandomizeResult


class PipelineError(Exception):
    """Raised for any user-facing pipeline failure, with a plain-
    language message suitable for direct display in the GUI."""
    pass


@dataclass
class ToolPaths:
    cripaktools_exe: Path
    node_exe: Path
    sgott_js: Path


@dataclass
class PipelineResult:
    seed_used: int
    stats: dict
    backup_dir: Optional[Path]
    staged_output_dir: Path
    installed: bool


def _build_extraction_callback(install_info: InstallInfo, tools: ToolPaths,
                                 progress_callback: Optional[Callable] = None):
    """
    Returns the extraction_callback that baseline_cache.cache_baseline
    will invoke on first run (or forced re-cache). Populates cache_dir
    with: WEAPONTABLE.json, WEAPONTEXT_EN.json, and a weapon_json/
    folder containing every convertible weapon .sgo as JSON — vanilla
    always included, mod's own additions layered on top if detected.
    """

    def report(msg):
        if progress_callback:
            progress_callback(msg)

    def callback(cache_dir: Path):
        # If Mods/WEAPON already contains THIS tool's own prior output,
        # extracting from it as if it were fresh mod content would
        # re-randomize already-randomized data — confirmed via a real
        # crash (stat multipliers compound, behavior gets transplanted
        # twice). Rather than just blocking and requiring the user to
        # manually find and delete a hidden file, automatically restore
        # from the marker's OWN linked backup (the exact "before" state
        # this install overwrote) and proceed — fully automatic in the
        # normal case. Only falls back to a hard error if that specific
        # backup can't actually be found, since we won't guess at
        # restoring from anything else.
        marker_path = install_info.root_path / "Mods" / "WEAPON" / MARKER_FILENAME
        if marker_path.exists():
            marker_data = read_own_marker(install_info.root_path)
            linked_backup = marker_data.get("backup_dir")
            if linked_backup and Path(linked_backup).exists():
                report("Detected previous output from this tool — "
                       "automatically restoring original content first...")
                restore_backup(install_info.root_path, Path(linked_backup), report)
            else:
                raise PipelineError(
                    "Mods/WEAPON already contains this tool's own previous "
                    "output, and the backup it should auto-restore from "
                    f"({linked_backup}) can't be found. Restore your "
                    "original mod content manually before running again."
                )

        vanilla_dir = cache_dir / "vanilla_extraction"
        weapon_json_dir = cache_dir / "weapon_json"
        weapon_json_dir.mkdir(parents=True, exist_ok=True)

        report("Extracting base game files...")
        root_cpk = install_info.root_path / "Root.cpk"
        run_cripaktools(tools.cripaktools_exe, root_cpk, vanilla_dir)

        vanilla_weapon_dir = vanilla_dir / "WEAPON"

        # Decide which WEAPONTABLE/WEAPONTEXT is authoritative
        if install_info.has_override_weapon_table:
            table_source_dir = install_info.root_path / "Mods" / "WEAPON"
        else:
            table_source_dir = vanilla_weapon_dir

        report("Reading weapon table...")
        table_sgo = table_source_dir / "WEAPONTABLE.SGO"
        text_sgo = table_source_dir / "WEAPONTEXT.EN.SGO"
        table_json = cache_dir / "WEAPONTABLE.json"
        text_json = cache_dir / "WEAPONTEXT_EN.json"

        convert_result = convert_batch(
            tools.node_exe, tools.sgott_js,
            [(table_sgo, table_json), (text_sgo, text_json)],
            max_workers=1,  # small, fixed set — no need for concurrency here
        )
        if convert_result.failed:
            raise PipelineError(
                f"Could not read the weapon table: {convert_result.failed[0][1]}"
            )

        # Always convert every vanilla weapon .sgo — needed as the base
        # file source even when a mod is installed, since many of the
        # mod's own table entries still reference original vanilla
        # filenames that only exist here, not in Mods/WEAPON.
        report("Converting base weapon files...")
        vanilla_pairs = []
        if vanilla_weapon_dir.exists():
            for sgo_file in vanilla_weapon_dir.glob("*.sgo"):
                out = weapon_json_dir / (sgo_file.stem + ".json")
                vanilla_pairs.append((sgo_file, out))
        vanilla_result = convert_batch(
            tools.node_exe, tools.sgott_js, vanilla_pairs, max_workers=4,
            progress_callback=lambda d, t: report(f"Converting base weapons... {d}/{t}"),
        )

        # If a mod is installed, layer its own added weapon files on
        # top of the vanilla set (same merge approach validated with
        # WEAPON_JSON_ALL earlier). Skip the table/text files
        # themselves, already handled above.
        mod_result = None
        if install_info.has_override_weapon_table:
            report("Converting mod-added weapon files...")
            mod_weapon_dir = install_info.root_path / "Mods" / "WEAPON"
            mod_pairs = []
            # Same filename exclusions validated in the original manual
            # pipeline — vehicle sub-components, enemy files, and DLC
            # vehicle parts are never referenced by real player-facing
            # WEAPONTABLE entries, so converting them is pure wasted
            # time. This was dropped during refactoring and is being
            # restored here after being caught in real-machine testing.
            import re as _re
            exclude_patterns = [
                r"^EWEAPON", r"^MOD_Vehicle", r"^MOD_WPNR",
                r"_DLC", r"^DLC_VEHICLE", r"^V\d+",
            ]
            for sgo_file in mod_weapon_dir.glob("*.sgo"):
                name_upper = sgo_file.stem.upper()
                if name_upper == "WEAPONTABLE" or name_upper.startswith("WEAPONTEXT"):
                    continue
                if any(_re.match(p, sgo_file.stem, _re.IGNORECASE) or
                       _re.search(p, sgo_file.stem, _re.IGNORECASE)
                       for p in exclude_patterns):
                    continue
                out = weapon_json_dir / (sgo_file.stem + ".json")
                mod_pairs.append((sgo_file, out))
            mod_result = convert_batch(
                tools.node_exe, tools.sgott_js, mod_pairs, max_workers=4,
                progress_callback=lambda d, t: report(f"Converting mod weapons... {d}/{t}"),
            )

        report("Extraction complete.")
        return vanilla_result, mod_result

    return callback


def install_staged_result(
    install_root: Path,
    result: PipelineResult,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """
    Installs an already-staged (do_install=False) result, without
    re-running extraction/randomization. Enables a clean 'preview then
    install' flow: call run_pipeline(do_install=False), let the user
    inspect result.stats, then call this to actually commit it —
    rather than re-running the whole pipeline a second time just to
    flip do_install to True.
    """
    def report(msg):
        if progress_callback:
            progress_callback(msg)

    run_id = result.staged_output_dir.name
    staged_sgo_dir = result.staged_output_dir / "sgo"
    return _backup_and_install(install_root, staged_sgo_dir, run_id, result.seed_used, report)


def run_pipeline(
    install_path_str: str,
    tools: ToolPaths,
    seed: Optional[int] = None,
    force_recache: bool = False,
    do_install: bool = True,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> PipelineResult:
    """
    Full end-to-end run: validate install, ensure clean baseline,
    discover archetypes, randomize, stage output, optionally install.

    do_install=False lets a caller preview/inspect the staged output
    before committing anything to the live game folder — useful for
    testing and for a future "preview before install" GUI step.

    If Mods/WEAPON currently contains this tool's own prior output, a
    fresh extraction automatically restores from that install's own
    paired backup first (see _build_extraction_callback) — no manual
    intervention needed in the normal case.
    """

    def report(msg):
        if progress_callback:
            progress_callback(msg)

    info = validate_install_path(install_path_str)
    if not info.is_valid:
        raise PipelineError(info.error_message)

    mode = "mod_installed" if info.has_override_weapon_table else "vanilla"
    report(f"Detected: {mode.replace('_', ' ')}")

    extraction_callback = _build_extraction_callback(info, tools, progress_callback)
    cache_result = cache_baseline(info.root_path, mode, extraction_callback, force=force_recache)

    if cache_result.was_freshly_extracted:
        report("Baseline extraction complete.")
    else:
        report("Using previously cached baseline (use force_recache to redo extraction).")

    with open(cache_result.baseline_dir / "WEAPONTABLE.json", encoding="utf-8") as f:
        table_data = json.load(f)
    with open(cache_result.baseline_dir / "WEAPONTEXT_EN.json", encoding="utf-8") as f:
        text_data = json.load(f)

    table_entries = table_data["variables"][0]["value"]
    text_entries = text_data["variables"][0]["value"]

    # Direct content check, not a proxy: scan the ACTUAL data about to
    # be used for signs it's already randomized. Checking the live
    # folder's marker file isn't enough — if the folder was corrupted,
    # cached that way, then correctly restored to clean content, the
    # marker would be gone from the LIVE folder while the CACHE (what
    # actually gets used below) still holds the old, already-tagged
    # data. This checks the real thing that matters — the content the
    # randomizer is about to read — regardless of whether it came from
    # a fresh extraction or a stale cache.
    known_tags = ("[Uncommon]", "[Rare]", "[Epic]", "[Legendary]")
    already_tagged_count = sum(
        1 for e in text_entries
        if any(e["value"][0]["value"].startswith(tag) for tag in known_tags)
    )
    # Fixed, low threshold rather than a percentage of dataset size —
    # genuinely clean vanilla/mod data should NEVER contain these tags
    # at all, so there's no legitimate reason to require a large count
    # before flagging it. Percentage-scaling actually made this LESS
    # sensitive on large tables, which is backwards for a check that
    # exists specifically to catch corruption early. Confirmed via
    # testing: a percentage-based threshold missed a real stale-cache
    # scenario that a fixed low threshold catches correctly.
    if already_tagged_count >= 3:
        raise PipelineError(
            f"The weapon data about to be used already shows {already_tagged_count} "
            f"previously-randomized names (e.g. '[Rare] ...'). This usually means "
            f"the tool's CACHED baseline is stale — even if you've restored your "
            f"live Mods/WEAPON folder to clean content, the cache from before that "
            f"restore may still hold old, already-randomized data. Clear the cache "
            f"folder (~/.edf_randomizer_tool/baseline_cache) and run again."
        )

    weapon_json_dir = cache_result.baseline_dir / "weapon_json"
    loader = make_directory_loader(weapon_json_dir)

    report("Analyzing weapon behaviors...")
    discovery = discover_archetypes(table_entries, loader)
    report(f"Found {discovery.total_categories} categories, "
           f"{discovery.total_weapons_processed} weapons analyzed.")

    if seed is None:
        seed = generate_random_seed()
    report(f"Using seed: {seed}")

    user_settings, settings_warning = load_settings(get_app_root())
    if settings_warning:
        report(settings_warning)

    report("Randomizing...")
    result: RandomizeResult = randomize_full_roster(
        table_entries, text_entries, discovery.summary, loader, seed=seed,
        settings=user_settings,
    )
    report(f"Randomized {result.stats['processed']} weapons.")

    # Stage output in its own run-specific folder — nothing touches the
    # live game folder until (and unless) install is explicitly run.
    run_id = make_run_id()
    staging_dir = _cache_root_for_staging() / run_id
    staged_json_dir = staging_dir / "json"
    staged_sgo_dir = staging_dir / "sgo"
    staged_json_dir.mkdir(parents=True, exist_ok=True)
    staged_sgo_dir.mkdir(parents=True, exist_ok=True)

    report("Preparing output files...")
    convert_pairs = []
    for filename, sgo_data in result.output_sgo_by_filename.items():
        json_path = staged_json_dir / filename
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(sgo_data, f, indent=2, ensure_ascii=False)
        sgo_out_name = filename[:-5] + ".sgo" if filename.endswith(".json") else filename + ".sgo"
        convert_pairs.append((json_path, staged_sgo_dir / sgo_out_name))

    text_data_out = dict(text_data)
    text_data_out["variables"] = [dict(text_data["variables"][0])]
    text_data_out["variables"][0]["value"] = result.output_text_entries
    text_json_path = staging_dir / "WEAPONTEXT_EN.json"
    with open(text_json_path, "w", encoding="utf-8") as f:
        json.dump(text_data_out, f, indent=2, ensure_ascii=False)
    convert_pairs.append((text_json_path, staged_sgo_dir / "WEAPONTEXT.EN.SGO"))

    report(f"Converting {len(convert_pairs)} files back to game format...")
    conv_result = convert_batch(
        tools.node_exe, tools.sgott_js, convert_pairs, max_workers=4,
        progress_callback=lambda d, t: report(f"Converting output... {d}/{t}"),
    )
    if conv_result.failed:
        raise PipelineError(
            f"{len(conv_result.failed)} of {conv_result.total} files failed to "
            f"convert. First error: {conv_result.failed[0][1]}"
        )

    backup_dir = None
    if do_install:
        backup_dir = _backup_and_install(info.root_path, staged_sgo_dir, run_id, seed, report)

    with open(staging_dir / "seed.txt", "w") as f:
        f.write(str(seed))

    return PipelineResult(
        seed_used=seed,
        stats=result.stats,
        backup_dir=backup_dir,
        staged_output_dir=staging_dir,
        installed=do_install,
    )


def _cache_root_for_staging() -> Path:
    return Path.home() / ".edf_randomizer_tool" / "staging"


def _backup_and_install(install_root: Path, staged_sgo_dir: Path, run_id: str,
                          seed: int, report: Callable[[str], None]) -> Path:
    """Backs up the live Mods/WEAPON folder before overwriting anything,
    then copies staged output in. Backup happens unconditionally and
    first, so a failure partway through installation never leaves the
    user without a way back."""
    live_weapon_dir = install_root / "Mods" / "WEAPON"
    live_weapon_dir.mkdir(parents=True, exist_ok=True)

    backup_root = Path.home() / ".edf_randomizer_tool" / "backups"
    backup_dir = backup_root / f"backup_{run_id}_{int(time.time())}"

    report("Backing up current weapon files...")
    if live_weapon_dir.exists() and any(live_weapon_dir.iterdir()):
        shutil.copytree(live_weapon_dir, backup_dir)
    else:
        backup_dir.mkdir(parents=True, exist_ok=True)

    report("Installing randomized weapons...")
    for f in staged_sgo_dir.glob("*.sgo") if any(True for _ in staged_sgo_dir.glob("*.sgo")) else []:
        shutil.copy2(f, live_weapon_dir / f.name)
    # also handle .SGO case-variants explicitly since Windows filesystems
    # are case-insensitive but glob patterns here are not guaranteed to be
    for f in staged_sgo_dir.iterdir():
        if f.suffix.lower() == ".sgo":
            shutil.copy2(f, live_weapon_dir / f.name)

    # Write the self-detection marker LAST, after install genuinely
    # succeeds — so any future run can tell this content is our own
    # prior output and refuse to treat it as fresh source data. This is
    # what closes the gap that caused a real crash: re-randomizing
    # already-randomized content, compounding stat multipliers and
    # double-transplanting behavior. Also links to its own paired
    # backup_dir (the exact "before" state this install overwrote), so
    # a future run can automatically restore and proceed rather than
    # requiring manual intervention every time.
    marker_path = live_weapon_dir / MARKER_FILENAME
    with open(marker_path, "w", encoding="utf-8") as f:
        json.dump({
            "tool": "edf_randomizer_tool",
            "installed_at": time.time(),
            "seed": seed,
            "backup_dir": str(backup_dir),
            "warning": "This folder contains randomized output from "
                       "edf_randomizer_tool. Do not use as a source for "
                       "re-randomization — restore original mod content first.",
        }, f, indent=2)

    report(f"Install complete. Backup saved at: {backup_dir}")
    return backup_dir


def restore_backup(install_root: Path, backup_dir: Path,
                     report: Optional[Callable[[str], None]] = None) -> None:
    """Restores a previous backup, undoing an install completely."""
    def _report(msg):
        if report:
            report(msg)

    if not backup_dir.exists():
        raise PipelineError(f"Backup folder not found: {backup_dir}")

    live_weapon_dir = install_root / "Mods" / "WEAPON"
    _report("Restoring previous weapon files...")
    if live_weapon_dir.exists():
        shutil.rmtree(live_weapon_dir)
    shutil.copytree(backup_dir, live_weapon_dir)
    _report("Restore complete.")
