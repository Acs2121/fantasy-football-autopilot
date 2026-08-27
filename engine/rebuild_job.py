"""
Runs a long refresh on a background thread, with progress the UI can poll.

The full data refresh downloads several megabytes from nflverse and Sleeper and
takes a few minutes, so it can't block an HTTP request.

The job itself knows nothing about what the work is -- the caller hands it a
callable that receives a `progress(message)` function. That keeps app state
(the draft, stat components) out of this module.
"""

import logging
import threading


class RebuildJob:

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self.state = "idle"      # idle | running | success | error
        self.message = ""
        self.report = None

    # ── public API ────────────────────────────────────────────────────────────

    def status(self):
        with self._lock:
            return {
                "state": self.state,
                "message": self.message,
                "report": self.report,
            }

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, work):
        """Run `work(progress)` on a background thread.

        `work` should return a report dict, or raise. Anything it raises is
        surfaced verbatim -- a refresh that half-failed must not look like it
        succeeded.
        """
        if self.is_running():
            return False
        self._set("running", "Starting...")
        with self._lock:
            self.report = None
        self._thread = threading.Thread(target=self._run, args=(work,), daemon=True)
        self._thread.start()
        return True

    # ── internals ─────────────────────────────────────────────────────────────

    def _set(self, state, message):
        with self._lock:
            self.state = state
            self.message = message

    def progress(self, message):
        self._set("running", message)

    def _run(self, work):
        # Mirror the rebuild script's own logging into the status message, so
        # the UI shows real progress instead of an opaque spinner.
        handler = _StatusHandler(self)
        log = logging.getLogger("rebuild")
        log.addHandler(handler)
        try:
            report = work(self.progress)
            with self._lock:
                self.report = report
            self._set("success", (report or {}).get("summary", "Refresh complete."))
        except Exception as exc:
            self._set("error", f"{type(exc).__name__}: {exc}")
        finally:
            log.removeHandler(handler)


class _StatusHandler(logging.Handler):
    """Feeds the rebuild script's log lines through as progress messages."""

    def __init__(self, job):
        super().__init__(level=logging.INFO)
        self._job = job

    def emit(self, record):
        try:
            msg = record.getMessage().strip()
        except Exception:
            return
        if msg and not msg.startswith("---"):
            self._job.progress(msg)
