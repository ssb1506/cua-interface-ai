"""
Mocked operator console (deliberately bare per the assignment's scope note
- section 3.6). A real console would be a co-browsing UI; this is a CLI
that proves the underlying control-transfer mechanism is real: it attaches
to the SAME live browser session the automation paused, over CDP, not a
fresh one.

Usage:
    python operator_console.py <evidence_run_dir>

It will:
  1. Read intervention_request.json from the run dir to get the CDP endpoint
     and context.
  2. Connect to that live session and show the current page/state.
  3. Let the operator run simple manual commands against the SAME page.
  4. On `resume`, write resume_signal.json so automation continues.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(run_dir_str: str):
    run_dir = Path(run_dir_str)
    req_path = run_dir / "intervention_request.json"
    if not req_path.exists():
        print(f"No intervention_request.json in {run_dir}")
        return
    req = json.loads(req_path.read_text())
    print("=== Intervention request ===")
    print(json.dumps(req, indent=2))
    print()

    actions_taken = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(req["cdp_endpoint"])
        context = browser.contexts[0]
        page = context.pages[0]
        print(f"Attached to LIVE session. Current URL: {page.url}")
        print("Commands: click <role> <name> | fill <role> <name> <value> | show | resume | quit")

        while True:
            try:
                cmd = input("operator> ").strip()
            except EOFError:
                break
            if not cmd:
                continue
            parts = cmd.split(" ", 3)
            parts = [("" if p == '""' else p) for p in parts]
            op = parts[0]

            if op == "show":
                print(f"URL: {page.url}\nTitle: {page.title()}")
            elif op == "click" and len(parts) >= 3:
                role, name = parts[1], parts[2]
                page.get_by_role(role, name=name, exact=False).first.click()
                actions_taken.append({"action": "click", "role": role, "name": name})
                print("clicked.")
            elif op == "fill" and len(parts) >= 4:
                role, name, value = parts[1], parts[2], parts[3]
                page.get_by_role(role, name=name, exact=False).first.fill(value)
                actions_taken.append({"action": "fill", "role": role, "name": name, "value": "[operator-entered]"})
                print("filled.")
            elif op == "resume":
                (run_dir / "resume_signal.json").write_text(json.dumps({
                    "handed_back": True,
                    "actions_taken": actions_taken,
                    "final_url": page.url,
                }, indent=2))
                print("Resume signal written. Handing control back to automation.")
                break
            elif op == "quit":
                break
            else:
                print("unrecognized command.")

        # Do NOT close the browser - it belongs to automation, we only
        # attached to it. Closing it here would break the handoff-back.


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python operator_console.py <evidence_run_dir>")
        sys.exit(1)
    main(sys.argv[1])
