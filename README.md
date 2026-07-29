# EDF6 Weapon Randomizer

A "loot-everything-is-different" randomizer for Earth Defense Force 6. Every player weapon keeps its own name, icon, and model — but its behavior and stats get randomly reassigned, grouped into five rarity tiers (Common → Legendary). Works standalone on vanilla EDF6, and automatically builds on top of EDF6.9x if it's installed.

**Status: pre-release / testing build.** This tool is built on top of EDF6.9x's weapon table when present, and its creator has not yet confirmed permission for public distribution. Please don't redistribute this further until that's resolved.

---

## Requirements

Install these first if you don't already have them — standard prerequisites for this kind of EDF6 mod, nothing specific to this tool:

- **EDFModLoader** — [link to be added]
- **.NET Framework** — required for the bundled extraction tool to run. If you already run other EDF6 mods, you almost certainly have this already.

## Installation

1. Download and extract this zip anywhere you like — it doesn't need to go inside your game folder.
2. That's it. Everything the tool needs (Node.js runtime, extraction tools) is bundled inside — nothing else to install.

## Usage

1. Run `EDF6Randomizer.exe`.
2. Click **Select EDF6 Folder...** and choose your EDF6 install folder (the one containing `Root.cpk`).
3. (Optional) Enter a specific seed if you want a reproducible result — leave it blank for a fresh random roll every time.
4. Click **Run Randomizer** and wait for it to finish. First run takes longer (extracting and converting the full weapon set); later runs reuse that extraction and are much faster.
5. Review the summary — tier breakdown, how many weapons got new behavior vs. just new stats.
6. Click **Install** to actually apply it to your game. This automatically backs up whatever was there before.
7. Play. If you want a different roll later, just run the tool again — it detects and safely handles its own previous output automatically, no manual cleanup needed.
8. **Restore Previous** (shown after installing) reverts to exactly what was there before that install.

## Customizing the randomizer (`settings.json`)

A `settings.json` file sits next to the exe — open it in any text editor to change how the randomizer behaves, no code or rebuilding required. It's plain JSON; save your changes and just run the tool again.

You can adjust:

- **`tiers`** — the five rarity tiers themselves: names, display tags, how strong each tier's stat boost is (`multiplier_min`/`multiplier_max`), and how common each one is (`weight`). Weights don't need to add up to any specific total — they're automatically scaled proportionally, so `1, 1, 1, 1, 1` gives equal odds just as correctly as `20, 20, 20, 20, 20` does.
- **`swappable_behavior_fields`** — which weapon properties (fire type, ammo type, burst pattern, lock-on behavior, etc.) are eligible to swap between weapons.
- **`stats_higher_is_better`** / **`stats_lower_is_better`** — which numeric stats get scaled by tier, and which direction counts as an improvement (e.g. more damage is better, less reload time is better).

If the file has a mistake in it (invalid JSON, or a value that doesn't make sense), the tool falls back to its original default settings automatically and tells you so when you run it — it won't just fail silently or crash.

**Not adjustable, on purpose**: whether thrown weapons (grenades) can donate their behavior to non-thrown weapons. This one caused a real, confirmed crash during testing and stays fixed for safety — every other combination in `swappable_behavior_fields` is open for you to experiment with.

## A Windows security warning will likely appear

This is a small, independently-built tool, so Windows SmartScreen or your antivirus may flag it as unrecognized. This is common for community modding tools and not a sign of anything malicious — click **"More info" → "Run anyway"** to proceed.

**For anyone who wants to verify this directly**: the complete Python source code is included in this release (see the `source/` folder) — every part of what this tool does is readable there, nothing is hidden inside the compiled `.exe`.

## Important compatibility notes

- **Not compatible with other weapon-modifying mods installed at the same time** — this tool and any other mod that touches `Mods/WEAPON/` will silently conflict with each other, file by file.
- **Multiplayer**: everyone in a session needs the exact same installed weapon data. If you want to play with friends, share your seed number with them and have them use it too, and make sure everyone's `settings.json` matches if you've customized it.

## Known limitations

- The in-game equip-screen stat display doesn't reflect every changed stat — specifically Rate of Fire, Explosion damage, and Recoil can change in actual gameplay without the displayed number updating to match. The real behavior is correct; only that specific preview number can lag behind.
- Equipment-class items (shields, boosters, exoskeletons) get a rarity tier and stat scaling, but not the same functional behavior variety that guns get — their internal stat fields work differently and aren't fully mapped yet.
- Beyond the thrown-weapon restriction above, other cross-class behavior pairings (allowed by default in `swappable_behavior_fields`) are mostly untested — if you hit something that seems broken, note exactly what the weapon looked like and what it seemed to be doing when it happened.

## Credits

Built on top of [EDF6.9x](https://www.nexusmods.com/earthdefenseforce6/mods/111) by ghostzerox30. Uses [sgott](https://github.com/zeddidragon/sgott) for SGO file conversion and [CriPakTools](https://github.com/esperknight/CriPakTools) for archive extraction.
