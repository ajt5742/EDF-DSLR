"""
edf_randomizer_tool / gui_state.py
------------------------------------------------------------
Pure Python state, validation, and formatting logic for the GUI —
deliberately kept free of any tkinter dependency, so it's fully
testable. The actual gui_app.py (tkinter widgets) should be a thin
layer that just calls into this module and displays the results.

This split matters because tkinter cannot be installed/tested in this
sandbox at all (network-restricted). Everything in THIS file can and
has been tested directly. gui_app.py cannot be verified the same way
and needs real-machine testing, same as the real tool binaries did.
"""

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from config import validate_install_path, describe_detected_state, InstallInfo


class AppPhase(Enum):
    IDLE = auto()               # no folder selected yet
    PATH_INVALID = auto()       # folder selected but doesn't look like EDF6
    READY = auto()              # valid folder selected, ready to run — includes
                                 # folders already containing this tool's own
                                 # prior output, since run_pipeline now
                                 # automatically restores from that install's
                                 # own paired backup before extracting, rather
                                 # than requiring the folder to be pre-blocked
    RUNNING = auto()            # pipeline actively running
    STAGED = auto()             # run finished, do_install was False, awaiting install decision
    INSTALLED = auto()          # run finished and installed
    ERROR = auto()              # something failed


@dataclass
class AppState:
    phase: AppPhase = AppPhase.IDLE
    install_path: Optional[str] = None
    install_info: Optional[InstallInfo] = None
    seed_text: str = ""
    use_manual_seed: bool = False
    last_error: str = ""
    last_result_summary: str = ""


def handle_folder_selected(state: AppState, folder_path: str) -> AppState:
    """Pure transition: user picked a folder via the browse dialog."""
    info = validate_install_path(folder_path)
    phase = AppPhase.PATH_INVALID if not info.is_valid else AppPhase.READY
    new_state = AppState(
        phase=phase,
        install_path=folder_path,
        install_info=info,
        seed_text=state.seed_text,
        use_manual_seed=state.use_manual_seed,
    )
    return new_state


def get_detection_message(state: AppState) -> str:
    if state.install_info is None:
        return "Select your EDF6 install folder to begin."
    return describe_detected_state(state.install_info)


def get_run_button_enabled(state: AppState) -> bool:
    return state.phase == AppPhase.READY


def validate_seed_field(raw_text: str) -> tuple:
    """
    Real-time validation for the seed input field, called on every
    keystroke by the GUI. Returns (is_valid, feedback_message) so the
    field can show a green check / red warning as the user types,
    rather than only failing at run time.
    """
    raw_text = raw_text.strip()
    if not raw_text:
        return True, "A fresh random seed will be used."
    if raw_text.lstrip("-").isdigit():
        return True, f"Will use exact seed: {raw_text}"
    if len(raw_text) > 200:
        return False, "That's too long for a seed — try something shorter."
    return True, f"Will use a seed derived from: \"{raw_text}\""


def format_stats_summary(stats: dict) -> str:
    """Turns the raw stats dict from RandomizeResult into a compact,
    plain-language summary block for display in the results area."""
    lines = []
    lines.append(f"Randomized {stats.get('processed', 0)} weapons")
    fc = stats.get("functional_change", 0)
    po = stats.get("power_only", 0)
    total = fc + po
    if total > 0:
        pct = fc / total * 100
        lines.append(f"  {fc} got new behavior ({pct:.0f}%), {po} were stat-only changes")

    tier_counts = stats.get("tier_counts", {})
    if tier_counts:
        lines.append("")
        lines.append("Rarity breakdown:")
        for tier, count in tier_counts.items():
            lines.append(f"  {tier}: {count}")

    skipped = stats.get("skipped_excluded", 0) + stats.get("skipped_no_file", 0)
    if skipped:
        lines.append("")
        lines.append(f"({skipped} entries left untouched — enemy/vehicle content "
                      f"or nothing to work with)")

    return "\n".join(lines)


def format_error_for_display(error) -> str:
    """Turns an error into a message a non-technical user can actually
    act on, rather than a raw Python traceback. Accepts either an
    Exception or a plain string (errors crossing a real process
    boundary — via multiprocessing — arrive as strings, since arbitrary
    exception objects aren't reliably picklable)."""
    msg = str(error)
    if "not found" in msg.lower() or "no such file" in msg.lower():
        return (f"A required file couldn't be found:\n{msg}\n\n"
                f"This usually means one of the bundled tool paths is wrong, "
                f"or your EDF6 folder was moved.")
    return f"Something went wrong:\n{msg}"
