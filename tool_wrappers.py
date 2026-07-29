"""
edf_randomizer_tool / tool_wrappers.py
------------------------------------------------------------
Wraps the bundled CriPakTools.exe and sgott.js as subprocesses, using
ONLY the exact command patterns we directly verified working during
real sessions against the real game:

  VERIFIED:   CriPakTools.exe root.cpk ALL
              (run with Root.cpk copied next to the exe)
  VERIFIED:   node sgott.js <input> <output>
              (same pattern for both .sgo->.json and .json->.sgo;
              sgott.js appears to auto-detect direction from the
              input file's extension)

NOT verified, and explicitly not assumed: any batch/folder-mode API
inside sgott.js itself. We've only ever called it one file at a time.
The speed-up here comes from running multiple independent, single-file
subprocess calls IN PARALLEL from Python — a safe optimization that
doesn't require knowing anything about sgott.js's internals, since
each call is identical to the exact pattern already proven to work.

max_workers defaults conservatively and is easy to drop to 1 (fully
sequential, exactly matching what was manually verified) if parallel
invocation ever causes file-locking or resource issues on a real
machine — something we have NOT tested and should verify before
shipping with a higher default.
"""

import sys
import shutil
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed


class ToolExecutionError(Exception):
    """Raised when a bundled tool fails, with captured output attached
    so a real error message can be surfaced instead of a silent hang
    or a generic traceback."""
    def __init__(self, command: str, returncode: int, stdout: str, stderr: str):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"Command failed (exit {returncode}): {command}\n"
            f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
        )


@dataclass
class ConversionResult:
    total: int
    succeeded: int
    failed: List[Tuple[Path, str]]  # (file, error message) for each failure


def run_cripaktools(cripaktools_exe: Path, root_cpk: Path, extraction_dir: Path) -> None:
    """
    Extract Root.cpk using the bundled CriPakTools.exe, following the
    exact verified requirement: Root.cpk must sit next to the exe.

    extraction_dir is where we stage a working copy of both the exe
    and Root.cpk, since we don't want to touch the user's actual game
    files or require write access inside Steam's install folder.
    """
    extraction_dir.mkdir(parents=True, exist_ok=True)
    staged_exe = extraction_dir / cripaktools_exe.name
    staged_cpk = extraction_dir / "Root.cpk"

    if not staged_exe.exists():
        shutil.copy2(cripaktools_exe, staged_exe)
    shutil.copy2(root_cpk, staged_cpk)

    result = subprocess.run(
        [str(staged_exe), "root.cpk", "ALL"],
        cwd=str(extraction_dir),
        capture_output=True,
        text=True,
        creationflags=_SUBPROCESS_CREATION_FLAGS,
    )
    if result.returncode != 0:
        raise ToolExecutionError(
            f"{staged_exe.name} root.cpk ALL",
            result.returncode, result.stdout, result.stderr,
        )

    # Sanity check: extraction should have produced at least a WEAPON
    # folder. If not, something went wrong even if the exe returned 0.
    if not (extraction_dir / "WEAPON").exists():
        raise ToolExecutionError(
            f"{staged_exe.name} root.cpk ALL",
            0, result.stdout,
            "Extraction completed but no WEAPON folder was produced — "
            "output may be in an unexpected location.",
        )


CONVERSION_TIMEOUT_SECONDS = 20  # generous — real conversions take well
                                  # under a second each based on tonight's
                                  # real runs; this only exists to catch a
                                  # genuinely stuck process, not slow ones

# Prevents Windows from flashing a brand-new console window for every
# single subprocess call — a real, observed issue once packaged as a
# --windowed (no-console) exe: since the parent has no console of its
# own, each of the potentially 1000+ node.exe/CriPakTools.exe calls in
# a real run was getting its own briefly-visible console window,
# causing both the visual flashing and real slowdown from constantly
# creating/tearing down windows. CREATE_NO_WINDOW only exists on
# Windows — guarded so this stays safe to test on other platforms too.
_SUBPROCESS_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _convert_one(node_exe: Path, sgott_js: Path, input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [str(node_exe), str(sgott_js), str(input_path), str(output_path)],
            capture_output=True,
            text=True,
            timeout=CONVERSION_TIMEOUT_SECONDS,
            creationflags=_SUBPROCESS_CREATION_FLAGS,
        )
    except subprocess.TimeoutExpired:
        # Without this, a single stuck conversion (e.g. unusual data
        # the converter can't handle) would block subprocess.run()
        # forever — and since ThreadPoolExecutor waits for every
        # submitted task before it can finish, one hung file freezes
        # the ENTIRE batch indefinitely with no error and no recovery.
        # Observed for real during testing near the end of a large
        # batch. Treat it as a normal per-file failure instead.
        raise ToolExecutionError(
            f"node sgott.js {input_path.name} {output_path.name}",
            -1, "",
            f"Timed out after {CONVERSION_TIMEOUT_SECONDS}s — this file may "
            f"contain data the converter couldn't handle.",
        )
    except OSError as e:
        # subprocess.run() itself can raise (e.g. FileNotFoundError /
        # WinError 2) if the executable can't be launched at all —
        # this happens BEFORE there's any return code to check, and is
        # NOT the same as the command running and failing. Observed in
        # real testing under concurrent load (many node.exe processes
        # launching at once) even when the same command succeeds
        # reliably when run alone — wrap it with the same context a
        # normal failure would have, rather than letting it escape
        # unwrapped and abort the whole batch.
        raise ToolExecutionError(
            f"node sgott.js {input_path.name} {output_path.name}",
            -1, "", f"Could not launch the conversion tool: {e}",
        )
    if result.returncode != 0:
        raise ToolExecutionError(
            f"node sgott.js {input_path.name} {output_path.name}",
            result.returncode, result.stdout, result.stderr,
        )
    if not output_path.exists():
        raise ToolExecutionError(
            f"node sgott.js {input_path.name} {output_path.name}",
            0, result.stdout,
            f"Conversion reported success but {output_path} was not created.",
        )


def convert_batch(
    node_exe: Path,
    sgott_js: Path,
    file_pairs: List[Tuple[Path, Path]],
    max_workers: int = 4,
    progress_callback: Optional[callable] = None,
) -> ConversionResult:
    """
    Convert many files using the SAME verified single-file command
    pattern, run concurrently. Never assumes a batch API exists inside
    sgott.js itself.

    progress_callback(done_count, total_count) — optional hook for a
    GUI progress bar later.
    """
    total = len(file_pairs)
    succeeded = 0
    failed: List[Tuple[Path, str]] = []
    done = 0

    if max_workers <= 1:
        # Fully sequential fallback — exactly the manually-verified
        # pattern, no concurrency at all. Safe default to fall back to
        # if parallel execution ever causes issues on a real machine.
        for input_path, output_path in file_pairs:
            try:
                _convert_one(node_exe, sgott_js, input_path, output_path)
                succeeded += 1
            except Exception as e:  # noqa: BLE001 — deliberately broad,
                # matching the parallel path below: _convert_one now
                # wraps known failure modes as ToolExecutionError, but
                # catching broadly here too means an unexpected error
                # type still fails just this one file, not the batch.
                failed.append((input_path, str(e)))
            done += 1
            if progress_callback:
                progress_callback(done, total)
        return ConversionResult(total, succeeded, failed)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_input = {
            executor.submit(_convert_one, node_exe, sgott_js, inp, out): inp
            for inp, out in file_pairs
        }
        for future in as_completed(future_to_input):
            input_path = future_to_input[future]
            try:
                future.result()
                succeeded += 1
            except Exception as e:  # noqa: BLE001 — deliberately broad,
                # see comment in the sequential path above.
                failed.append((input_path, str(e)))
            done += 1
            if progress_callback:
                progress_callback(done, total)

    return ConversionResult(total, succeeded, failed)
