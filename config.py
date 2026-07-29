"""
edf_randomizer_tool / config.py
------------------------------------------------------------
Shared config, paths, and install-detection logic for the packaged
randomizer tool. This is a NEW project, kept entirely separate from
the earlier standalone scripts (full_roster_randomize.py etc.) — none
of those are touched or overwritten by this.

This module has no GUI dependency — it's pure logic, testable and
reusable regardless of what front-end (tkinter now, something else
later) ends up calling it.
"""

import os
import sys
import json
import random
import string
from pathlib import Path
from dataclasses import dataclass


MARKER_FILENAME = ".edf_randomizer_marker.json"


@dataclass
class InstallInfo:
    """Result of validating/inspecting a user-selected EDF6 folder."""
    root_path: Path
    is_valid: bool
    has_root_cpk: bool
    has_override_weapon_table: bool  # True if Mods/WEAPON/WEAPONTABLE.SGO exists
    has_own_marker: bool = False     # True if Mods/WEAPON already contains
                                      # THIS tool's own prior output — treating
                                      # it as fresh source data would silently
                                      # re-randomize already-randomized content,
                                      # compounding stat multipliers and
                                      # double-transplanting behavior. Confirmed
                                      # via a real crash caused by exactly this.
    error_message: str = ""


def validate_install_path(selected_path: str) -> InstallInfo:
    """
    Check that a user-browsed folder actually looks like an EDF6 install,
    and detect whether a weapon-overriding mod (6.9x or otherwise) is
    already present — determines which extraction path we take later.

    Deliberately does NOT scan default Steam locations or guess at a
    path — the user always browses to it explicitly, per design.
    """
    root = Path(selected_path)

    if not root.exists() or not root.is_dir():
        return InstallInfo(root, False, False, False,
                            "That folder doesn't exist.")

    exe_candidates = ["EDF6.exe", "EDF.dll"]
    has_exe = any((root / name).exists() for name in exe_candidates)
    root_cpk_path = root / "Root.cpk"
    has_root_cpk = root_cpk_path.exists()

    if not has_exe and not has_root_cpk:
        return InstallInfo(root, False, False, False,
                            "That doesn't look like an EDF6 install folder "
                            "(no EDF6.exe or Root.cpk found there). "
                            "Please select the folder containing the game.")

    override_table_path = root / "Mods" / "WEAPON" / "WEAPONTABLE.SGO"
    has_override = override_table_path.exists()
    marker_path = root / "Mods" / "WEAPON" / MARKER_FILENAME
    has_marker = marker_path.exists()

    return InstallInfo(
        root_path=root,
        is_valid=True,
        has_root_cpk=has_root_cpk,
        has_override_weapon_table=has_override,
        has_own_marker=has_marker,
        error_message="",
    )


def read_own_marker(root_path) -> dict:
    """Reads details from a previously-written marker, if present —
    e.g. so an error message can say when/what seed was last installed."""
    marker_path = Path(root_path) / "Mods" / "WEAPON" / MARKER_FILENAME
    if not marker_path.exists():
        return {}
    try:
        with open(marker_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def describe_detected_state(info: InstallInfo) -> str:
    """Plain-language summary shown to the user after folder selection —
    this is the 'do I need 6.9x or not' auto-detection surfaced clearly,
    rather than silently assumed."""
    if not info.is_valid:
        return info.error_message
    if info.has_own_marker:
        return ("This folder's Mods/WEAPON currently contains output from "
                 "a previous run of this tool. Running again will "
                 "automatically restore the original content this install "
                 "replaced, then randomize fresh from there — no action "
                 "needed.")
    if info.has_override_weapon_table:
        return ("Detected an existing weapon-modifying mod installed "
                 "(e.g. EDF6.9x or similar). The randomizer will build on "
                 "top of whatever weapons are currently present.")
    return ("No weapon-modifying mod detected — the randomizer will run "
            "against the base game's own weapon roster.")


def generate_random_seed() -> int:
    """Fresh seed each run by default, pulled from OS entropy (not just
    the system clock, so two people launching at the same moment still
    get genuinely different rolls)."""
    return int.from_bytes(os.urandom(4), byteorder="big")


def parse_manual_seed(raw_text: str):
    """Accepts either a plain integer or an arbitrary string (hashed
    into a stable integer), so users can type something memorable
    ('my-seed-1') instead of needing to know a specific number."""
    raw_text = raw_text.strip()
    if not raw_text:
        return None
    if raw_text.lstrip("-").isdigit():
        return int(raw_text)
    # Non-numeric input: derive a stable integer seed from it so the
    # same text always reproduces the same result.
    digest = sum(ord(c) * (i + 1) for i, c in enumerate(raw_text))
    return digest


def make_run_id() -> str:
    """Short identifier for this run's staging/output folder, so
    repeated runs never collide with each other."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"run_{suffix}"


def get_app_root() -> Path:
    """
    The folder this program is actually running from — identical
    logic whether running as a plain script (python gui_app.py) or a
    PyInstaller-packaged executable (onedir mode). Shared by both
    get_bundled_tool_paths() and settings.py's settings.json location,
    so there's exactly one place that knows how to tell "packaged" from
    "dev script" apart.
    """
    if getattr(sys, "frozen", False):
        # Packaged executable (PyInstaller onedir): sys.executable is
        # the actual .exe on disk.
        return Path(sys.executable).parent
    # Plain script: resolve relative to THIS file, so this only gives
    # the right answer if config.py stays in the same folder as the
    # rest of the tool — true throughout this whole project.
    return Path(__file__).resolve().parent


def get_bundled_tool_paths() -> dict:
    """
    Resolves the bundled tool paths relative to wherever this program
    is actually running from — identical logic whether running as a
    plain script (python gui_app.py, used throughout development and
    testing) or a PyInstaller-packaged executable (onedir mode). Both
    cases use the SAME relative folder structure:

        <app_root>/
            gui_app.py (dev) or gui_app.exe (packaged)
            tools/
                CriPakTools.exe
                node/node.exe
                sgott/sgott.js

    Returns a plain dict rather than a ToolPaths object — ToolPaths is
    defined in orchestrator.py, which already imports FROM this module,
    so importing it back here would create a circular import. Callers
    build their own ToolPaths from these three paths.
    """
    app_root = get_app_root()
    tools_dir = app_root / "tools"
    return {
        "cripaktools_exe": tools_dir / "CriPakTools.exe",
        "node_exe": tools_dir / "node" / "node.exe",
        "sgott_js": tools_dir / "sgott" / "sgott.js",
    }
