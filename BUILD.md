# Building EDF6 Weapon Randomizer from Source

This document describes exactly how to build `EDF6Randomizer.exe` from the source code in this repository, so the compiled binary can be independently verified against the source.

## Overview

The application is a Python program (using `tkinter` for the GUI) packaged into a standalone Windows executable with **PyInstaller**. It bundles three external tools at runtime — none of which are compiled into the Python code itself, and all of which are independently verifiable open-source projects.

## Prerequisites

- Windows 10/11
- Python 3.11+ ([python.org](https://www.python.org/))
- Node.js (any current LTS) — needed only to prepare the bundled `sgott` tool, not to build the Python application itself

## Step 1: Get the source files

All `.py` files in this repository's root directory make up the application:

```
config.py
baseline_cache.py
tool_wrappers.py
archetype_discovery.py
randomizer_core.py
orchestrator.py
gui_state.py
background_runner.py
gui_app.py
settings.py
```

These must all be in the same folder — they import from each other directly.

## Step 2: Install the Python build dependency

```powershell
pip install pyinstaller
```

This is the only third-party Python package required. Everything else the application uses (`tkinter`, `multiprocessing`, `json`, `subprocess`, etc.) is part of Python's own standard library.

## Step 3: Prepare the bundled tools folder

The application calls three external command-line tools at runtime — via `subprocess`, exactly like calling them from a terminal. None of their code is compiled into our Python source; they must be placed in a `tools/` folder alongside the built executable:

```
tools/
    CriPakTools.exe
    node/
        node.exe
        (Node's other runtime files)
    sgott/
        sgott.js
        (its converter modules)
        node_modules/
            (sgott's runtime dependencies only)
```

**CriPakTools** — [github.com/esperknight/CriPakTools](https://github.com/esperknight/CriPakTools). Build the `.exe` from that repository directly (a small C# .NET Framework 4.0 tool), or use a build you've already compiled from its source.

**Node.js** — download the official portable Windows ZIP build from [nodejs.org](https://nodejs.org/en/download) (not the installer). Extract its contents into `tools/node/`.

**sgott** — [github.com/zeddidragon/sgott](https://github.com/zeddidragon/sgott). Clone it into `tools/sgott/`, then install only its production dependencies:

```powershell
cd tools/sgott
npm install --omit=dev
```

Two folders that come with the `sgott` repository are not needed at runtime and can be removed to reduce size (verified: no code path in `sgott.js` or its converter modules references either):
- `data/` — the author's own personal weapon-balance reference tables, unrelated to the actual SGO/JSON conversion logic
- `pkg`/`pkg-fetch` in `node_modules` — only used by `sgott`'s own optional standalone-executable build step (`npm run build-sgott`), never invoked by this application

## Step 4: Build the executable

From the folder containing the `.py` files:

```powershell
pyinstaller --onedir --windowed --name EDF6Randomizer --noconfirm --noupx gui_app.py
```

Flag rationale:
- `--onedir` — produces a folder (exe + supporting files) rather than a single self-extracting file, avoiding PyInstaller's more complex single-file runtime extraction behavior
- `--windowed` — suppresses the console window, since this is a GUI application
- `--noupx` — disables UPX compression; UPX's compression signature is independently associated with elevated antivirus false-positive rates for packaged executables, and disabling it has no functional effect on the application

## Step 5: Assemble the final output

```powershell
copy tools dist\EDF6Randomizer\tools /E /I
copy settings.json dist\EDF6Randomizer\settings.json
```

The result in `dist\EDF6Randomizer\` is the complete, runnable application: `EDF6Randomizer.exe`, its `_internal` support folder (created by PyInstaller), the `tools/` folder, and `settings.json`.

## Why the compiled build may trigger antivirus heuristics

This application is built with PyInstaller, which packages Python code into a self-extracting executable — a pattern that legitimate tools and malware packers both use, and which some heuristic/behavioral scanners cannot distinguish between from execution pattern alone. Static, signature-based antivirus engines do not flag this build (confirmed via VirusTotal). This is a well-documented, ongoing category of false positive; PyInstaller's own GitHub repository tracks it under an `antivirus-false-positives` issue label.
