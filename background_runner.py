"""
edf_randomizer_tool / background_runner.py
------------------------------------------------------------
Runs orchestrator.run_pipeline() in a SEPARATE OS PROCESS (not a
background thread), with progress/results communicated back via a
multiprocessing-safe queue.

Originally built using threading.Thread. Switched to multiprocessing
after real-machine testing showed a 100% reproducible failure — every
single subprocess call (node/sgott.js) failing with "cannot find the
file specified" — but ONLY when run from a background thread inside
the tkinter GUI process. The identical code, paths, and data succeeded
reliably every time when run as a plain script (run_test.py, main
thread, no GUI). A uniform total failure rate across all files points
to something about the thread-vs-GUI-process context itself, not
anything file-specific. Running the pipeline in a genuinely separate
process sidesteps that entirely — it's the same execution context
run_test.py already uses, just launched and monitored from the GUI
instead of run directly.

The GUI layer just needs to: call start_pipeline_run(), then poll
drain_events() periodically (e.g. via root.after(100, ...)) to get
progress messages and the final result without blocking.
"""

import multiprocessing
from dataclasses import dataclass
from typing import Optional

from orchestrator import run_pipeline, ToolPaths, PipelineResult, PipelineError


@dataclass
class ProgressEvent:
    message: str


@dataclass
class DoneEvent:
    result: PipelineResult


@dataclass
class ErrorEvent:
    error_message: str  # kept as a plain string — arbitrary exception
                         # objects aren't reliably picklable across the
                         # process boundary, but the message is all the
                         # GUI actually needs to display anyway.


def _child_process_entry(queue: multiprocessing.Queue, install_path: str,
                           tools: ToolPaths, seed: Optional[int],
                           force_recache: bool, do_install: bool):
    """Runs in the CHILD process. Must be a plain module-level function
    (not a closure/lambda) so it can be pickled by multiprocessing."""
    try:
        result = run_pipeline(
            install_path, tools, seed=seed,
            force_recache=force_recache, do_install=do_install,
            progress_callback=lambda msg: queue.put(ProgressEvent(msg)),
        )
        queue.put(DoneEvent(result))
    except Exception as e:  # noqa: BLE001 — deliberately broad: any
        # failure in the child process must reach the parent as an
        # ErrorEvent, not vanish silently when the process exits.
        queue.put(ErrorEvent(str(e)))


class PipelineRunner:
    """Wraps one pipeline run in a separate process. A fresh instance
    should be created per run (not reused), so there's no ambiguity
    about which run's events are in the queue."""

    def __init__(self):
        self._queue: multiprocessing.Queue = multiprocessing.Queue()
        self._process: Optional[multiprocessing.Process] = None

    def start(self, install_path: str, tools: ToolPaths, seed: Optional[int],
              force_recache: bool, do_install: bool):
        if self._process is not None:
            raise RuntimeError("This PipelineRunner has already been started — "
                                "create a new one for each run.")

        self._process = multiprocessing.Process(
            target=_child_process_entry,
            args=(self._queue, install_path, tools, seed, force_recache, do_install),
            daemon=True,
        )
        self._process.start()

    def drain_events(self) -> list:
        """Non-blocking: returns all events currently queued, in order.
        Call this periodically from the GUI's event loop."""
        events = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except Exception:  # queue.Empty, but multiprocessing.Queue
                # can also raise other exceptions during teardown —
                # treat any of them the same way: nothing more to drain.
                break
        return events

    def is_running(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def is_done(self) -> bool:
        return self._process is not None and not self._process.is_alive()

