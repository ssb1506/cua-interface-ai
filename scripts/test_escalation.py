"""
Exercises the escalation/handoff mechanism end-to-end without the LLM:
launches a live session, pretends automation got stuck, writes the
intervention request, and waits for an operator to resume it (run
operator_console.py against the printed run dir in another terminal/process).

Usage:
    python scripts/test_escalation.py
    # in another shell:
    python operator_console.py evidence/<printed-run-id>
    # in the operator console: fill textbox "" 12345
    #                           click button Search
    #                           resume
"""
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright
from agent import escalation

CDP_PORT = 9333
ROOT = Path(__file__).resolve().parent.parent
run_id = f"escalation_stuck_and_resume"
run_dir = ROOT / "evidence" / run_id
run_dir.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(args=[f"--remote-debugging-port={CDP_PORT}"])
    page = browser.new_page()
    page.goto("http://localhost:5055/")
    page.screenshot(path=str(run_dir / "stuck.png"))

    cdp_endpoint = f"http://localhost:{CDP_PORT}"
    escalation.request_intervention(
        run_dir,
        context={
            "capability_id": "lookup_savings_balance", "goal": "look up member and read balance",
            "reason": "simulated: agent could not decide next step (test harness)",
            "current_url": page.url,
        },
        cdp_endpoint=cdp_endpoint,
    )
    print(f"[automation] STUCK - wrote intervention_request.json in {run_dir}")
    print(f"[automation] In another terminal run:  python operator_console.py {run_dir}")
    print("[automation] waiting for operator resume signal...")

    resume_data = escalation.wait_for_resume(run_dir, poll_interval=1.0, timeout=180)
    print(f"[automation] RESUMED. Operator actions: {resume_data['actions_taken']}")
    print(f"[automation] Live page after handoff -> URL: {page.url}, title: {page.title()}")
    page.screenshot(path=str(run_dir / "after_resume.png"))
    browser.close()
    print("[automation] Done. This proves control transferred on the SAME session (same page object).")
