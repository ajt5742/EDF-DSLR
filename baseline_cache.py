"""
edf_randomizer_tool / baseline_cache.py
------------------------------------------------------------
Ensures every run (including re-rolls with a new seed) starts from a
clean, untouched extraction of the weapon data — never from whatever
currently happens to be sitting in the live Mods/WEAPON/ folder, which
could already be OUR OWN previous randomized output. Without this, a
second run would randomize an already-randomized state, compounding
into nonsense.

Design choice, stated plainly: staleness detection (did the user
update their mods since we cached?) is NOT attempted automatically —
that's a genuinely hard problem to get right silently, and getting it
wrong silently is worse than not trying. Instead, caching is
first-run-automatic, and re-caching is always available as an
explicit, user-visible action ("Re-extract baseline").

The actual extraction work (CriPakTools/sgott subprocess calls) is
NOT implemented here — this module takes it as an injected callback,
so the caching/skip/force logic can be fully tested right now without
needing the real bundled tools in place yet.
"""

import hashlib
import json
import shutil
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class CacheResult:
    baseline_dir: Path
    was_freshly_extracted: bool
    detected_mode: str  # "vanilla" or "mod_installed"
    cached_at: float


def _cache_root() -> Path:
    """Where all cached baselines live, kept entirely outside the game
    folder so it's untouched by game updates/verification and survives
    the tool being run against multiple different installs."""
    return Path.home() / ".edf_randomizer_tool" / "baseline_cache"


def get_cache_dir(install_path: Path) -> Path:
    """Deterministic, collision-free cache location per install path —
    so running the tool against two different EDF6 installs (e.g. two
    Steam libraries) never mixes their cached data."""
    path_hash = hashlib.sha256(str(install_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return _cache_root() / path_hash


def _metadata_path(cache_dir: Path) -> Path:
    return cache_dir / "_cache_metadata.json"


def has_cached_baseline(install_path: Path) -> bool:
    cache_dir = get_cache_dir(install_path)
    return _metadata_path(cache_dir).exists()


def read_cache_metadata(install_path: Path) -> Optional[dict]:
    cache_dir = get_cache_dir(install_path)
    meta_path = _metadata_path(cache_dir)
    if not meta_path.exists():
        return None
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def clear_cache(install_path: Path) -> None:
    cache_dir = get_cache_dir(install_path)
    if cache_dir.exists():
        shutil.rmtree(cache_dir)


def cache_baseline(
    install_path: Path,
    detected_mode: str,
    extraction_callback: Callable[[Path], None],
    force: bool = False,
) -> CacheResult:
    """
    Ensure a clean baseline extraction exists for this install, and
    return its location.

    extraction_callback: a function that takes the target cache
    directory and populates it with the actual extracted/converted
    weapon data (calls into CriPakTools/sgott — not implemented in
    this module). Only actually invoked if no cache exists yet, or
    force=True.
    """
    cache_dir = get_cache_dir(install_path)

    if has_cached_baseline(install_path) and not force:
        meta = read_cache_metadata(install_path)
        return CacheResult(
            baseline_dir=cache_dir,
            was_freshly_extracted=False,
            detected_mode=meta.get("detected_mode", detected_mode),
            cached_at=meta.get("cached_at", 0),
        )

    # Fresh extraction needed (first run, or explicit force re-cache).
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    extraction_callback(cache_dir)

    metadata = {
        "install_path": str(install_path.resolve()),
        "detected_mode": detected_mode,
        "cached_at": time.time(),
    }
    with open(_metadata_path(cache_dir), "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return CacheResult(
        baseline_dir=cache_dir,
        was_freshly_extracted=True,
        detected_mode=detected_mode,
        cached_at=metadata["cached_at"],
    )
