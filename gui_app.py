"""
edf_randomizer_tool / gui_app.py
------------------------------------------------------------
The actual window. Deliberately thin — every real decision (state
transitions, validation, formatting) delegates to gui_state.py, and
the pipeline itself runs through background_runner.py so the window
never freezes during multi-minute extraction/conversion steps.

IMPORTANT: this file could not be visually tested in the environment
it was built in (no display, tkinter itself unavailable to install).
The WIRING (does clicking Run call the right function with the right
arguments, does the progress log update correctly) was verified via
dependency-mocked tests — see test_gui_app.py. The actual visual
layout, window sizing, and on-screen appearance have NOT been
confirmed and need real verification on your machine, the same way
the bundled tool binaries needed real-machine verification earlier.
"""

import multiprocessing
import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path
from typing import Optional

from gui_state import (
    AppState, AppPhase, handle_folder_selected, get_detection_message,
    get_run_button_enabled, validate_seed_field, format_stats_summary,
    format_error_for_display,
)
from background_runner import PipelineRunner, ProgressEvent, DoneEvent, ErrorEvent
from orchestrator import ToolPaths, install_staged_result, restore_backup, PipelineResult
from config import get_bundled_tool_paths


class RandomizerApp:
    def __init__(self, root: tk.Tk, tools: ToolPaths):
        self.root = root
        self.tools = tools
        self.state = AppState()
        self.runner: Optional[PipelineRunner] = None
        self.last_result: Optional[PipelineResult] = None

        root.title("EDF6 Weapon Randomizer")
        root.geometry("640x520")

        # --- Folder selection ---
        folder_frame = ttk.Frame(root, padding=10)
        folder_frame.pack(fill="x")
        self.browse_button = ttk.Button(folder_frame, text="Select EDF6 Folder...",
                                          command=self._on_browse_clicked)
        self.browse_button.pack(side="left")
        self.folder_label = ttk.Label(folder_frame, text="No folder selected")
        self.folder_label.pack(side="left", padx=10)

        self.detection_label = ttk.Label(root, text=get_detection_message(self.state),
                                           wraplength=600, padding=(10, 0))
        self.detection_label.pack(fill="x")

        # --- Seed input ---
        seed_frame = ttk.Frame(root, padding=10)
        seed_frame.pack(fill="x")
        ttk.Label(seed_frame, text="Seed (optional):").pack(side="left")
        self.seed_var = tk.StringVar()
        self.seed_var.trace_add("write", self._on_seed_changed)
        self.seed_entry = ttk.Entry(seed_frame, textvariable=self.seed_var, width=30)
        self.seed_entry.pack(side="left", padx=5)
        self.seed_feedback_label = ttk.Label(seed_frame, text="A fresh random seed will be used.")
        self.seed_feedback_label.pack(side="left", padx=5)

        # --- Run button ---
        self.run_button = ttk.Button(root, text="Run Randomizer",
                                       command=self._on_run_clicked, state="disabled")
        self.run_button.pack(pady=5)

        # --- Progress log ---
        self.progress_text = tk.Text(root, height=15, state="disabled")
        self.progress_text.pack(fill="both", expand=True, padx=10, pady=5)

        # --- Result actions (hidden until a run completes) ---
        self.result_frame = ttk.Frame(root, padding=10)
        self.install_button = ttk.Button(self.result_frame, text="Install",
                                           command=self._on_install_clicked)
        self.install_button.pack(side="left", padx=5)
        self.restore_button = ttk.Button(self.result_frame, text="Restore Previous",
                                           command=self._on_restore_clicked, state="disabled")
        self.restore_button.pack(side="left", padx=5)

    # ---------- Folder selection ----------

    def _on_browse_clicked(self):
        folder = filedialog.askdirectory(title="Select your EDF6 install folder")
        if not folder:
            return
        self.state = handle_folder_selected(self.state, folder)
        self.folder_label.config(text=folder)
        self.detection_label.config(text=get_detection_message(self.state))
        self._update_run_button()

    def _update_run_button(self):
        enabled = get_run_button_enabled(self.state)
        self.run_button.config(state="normal" if enabled else "disabled")

    # ---------- Seed input ----------

    def _on_seed_changed(self, *_args):
        text = self.seed_var.get()
        _valid, message = validate_seed_field(text)
        self.seed_feedback_label.config(text=message)

    def _get_seed_value(self):
        from config import parse_manual_seed
        return parse_manual_seed(self.seed_var.get())

    # ---------- Running the pipeline ----------

    def _on_run_clicked(self):
        self._log_clear()
        self.run_button.config(state="disabled")
        self.browse_button.config(state="disabled")

        self.runner = PipelineRunner()
        self.runner.start(
            self.state.install_path, self.tools,
            seed=self._get_seed_value(),
            force_recache=False,
            do_install=False,  # always preview first — install is a separate explicit step
        )
        self.root.after(100, self._poll_runner)

    def _poll_runner(self):
        if self.runner is None:
            return
        for event in self.runner.drain_events():
            if isinstance(event, ProgressEvent):
                self._log_line(event.message)
            elif isinstance(event, DoneEvent):
                self.last_result = event.result
                self._log_line("")
                self._log_line(format_stats_summary(event.result.stats))
                self._show_result_actions()
                self.browse_button.config(state="normal")
            elif isinstance(event, ErrorEvent):
                self._log_line("")
                self._log_line(format_error_for_display(event.error_message))
                self.run_button.config(state="normal")
                self.browse_button.config(state="normal")

        if self.runner.is_running():
            self.root.after(100, self._poll_runner)

    # ---------- Install / Restore ----------

    def _show_result_actions(self):
        self.result_frame.pack(fill="x")

    def _on_install_clicked(self):
        if self.last_result is None:
            return
        self.install_button.config(state="disabled")
        backup_dir = install_staged_result(
            Path(self.state.install_path), self.last_result,
            progress_callback=self._log_line,
        )
        self._last_backup_dir = backup_dir
        self.restore_button.config(state="normal")
        self._log_line(f"Installed. Backup saved at: {backup_dir}")

    def _on_restore_clicked(self):
        if not hasattr(self, "_last_backup_dir"):
            return
        restore_backup(Path(self.state.install_path), self._last_backup_dir,
                        report=self._log_line)
        self.restore_button.config(state="disabled")

    # ---------- Progress log helpers ----------

    def _log_line(self, message: str):
        self.progress_text.config(state="normal")
        self.progress_text.insert("end", message + "\n")
        self.progress_text.see("end")
        self.progress_text.config(state="disabled")

    def _log_clear(self):
        self.progress_text.config(state="normal")
        self.progress_text.delete("1.0", "end")
        self.progress_text.config(state="disabled")


def main():
    tool_paths = get_bundled_tool_paths()
    tools = ToolPaths(
        cripaktools_exe=tool_paths["cripaktools_exe"],
        node_exe=tool_paths["node_exe"],
        sgott_js=tool_paths["sgott_js"],
    )
    root = tk.Tk()
    RandomizerApp(root, tools)
    root.mainloop()


if __name__ == "__main__":
    # MUST be the very first thing that runs, before anything else —
    # required for multiprocessing.Process to work correctly once this
    # is packaged into a Windows executable (PyInstaller). Without this,
    # a packaged exe's "child" process can end up re-launching the
    # entire GUI instead of just running the pipeline — effectively
    # reintroducing a version of the threading bug already fixed
    # tonight, in a new, packaging-specific form. Harmless no-op when
    # running as a plain script or on non-Windows platforms.
    multiprocessing.freeze_support()
    main()
