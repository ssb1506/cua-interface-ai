"""
Human-in-the-loop escalation & handoff.

Control-transfer model
-----------------------
The browser is launched with a remote-debugging (CDP) port open. That is
the seam: "control" of a live session is just "who currently holds a
CDP/Playwright connection to it and is issuing commands." Automation and a
human operator are just two different clients of the *same* underlying
browser process and page - never two separate sessions.

Handoff sequence:
  1. Automation hits a stuck/blocked/risky-needs-confirmation state.
  2. It writes an `intervention_request.json` (goal, capability, current
     step, reason, screenshot, and the CDP endpoint) into the run's
     evidence directory, then blocks on `wait_for_resume()`.
  3. A human (via `operator_console.py`, the mocked operator surface)
     connects to the SAME browser over the CDP endpoint, drives the SAME
     live page manually, and records what they did.
  4. The operator writes a `resume_signal.json` with a summary of actions
     taken and a `handed_back` flag.
  5. Automation's `wait_for_resume()` returns, and the caller (discovery
     loop or replay engine) re-observes the live page and either resumes
     the loop or finalizes the result - context and evidence carry across
     the handoff because it is literally the same page object process,
     nothing is re-created.

Scope note (per assignment Section 3.6): the operator console itself is a
bare CLI, deliberately mocked. The pause/cede/resume mechanism against the
live CDP-attached session is real, not mocked.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def request_intervention(run_dir: Path, context: dict[str, Any], cdp_endpoint: str) -> Path:
    payload = {
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "cdp_endpoint": cdp_endpoint,
        "status": "awaiting_operator",
        **context,
    }
    path = run_dir / "intervention_request.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def wait_for_resume(run_dir: Path, poll_interval: float = 1.0, timeout: Optional[float] = None) -> dict:
    """Blocks until the operator console writes resume_signal.json in the
    same run directory. In production this would be a webhook/queue; for
    this project a polled file is an honest, real, minimal stand-in for
    the same control-transfer semantics."""
    signal_path = run_dir / "resume_signal.json"
    waited = 0.0
    while not signal_path.exists():
        time.sleep(poll_interval)
        waited += poll_interval
        if timeout is not None and waited >= timeout:
            raise TimeoutError(f"No operator resume signal after {timeout}s in {run_dir}")
    data = json.loads(signal_path.read_text())
    (run_dir / "intervention_request.json").write_text(
        json.dumps({**json.loads((run_dir / "intervention_request.json").read_text()), "status": "resolved"}, indent=2)
    )
    return data
