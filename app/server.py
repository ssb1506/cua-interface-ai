"""
Mock 'legacy' bank back-office app used as the target surface for the
computer-use automation system.

Deliberately hostile-ish surface to stand in for the real environment:
- server-rendered HTML, no test IDs, no client-side framework
- a nested-table layout on the search results screen
- a multi-step flow: search -> member detail -> open sub-account -> confirm
- toggleable runtime failure modes via query params, so replay error-handling
  can be demonstrated deterministically (not-found, validation error,
  session-expired banner, permission-denied)

This is a stand-in for a real core-banking servicing screen. Not connected
to any real data. All member records are synthetic.
"""
from __future__ import annotations

import random
import string
from flask import Flask, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "dev-only-not-a-real-secret"

# --- synthetic in-memory "core banking" data -------------------------------

MEMBERS = {
    "12345": {"name": "Jordan Ellis", "savings_balance": "4,213.55", "status": "active"},
    "67890": {"name": "Priya Nair", "savings_balance": "912.10", "status": "active"},
    "11111": {"name": "Sam Osei", "savings_balance": "0.00", "status": "frozen"},
}

SUBACCOUNTS = {}  # member_id -> list of {id, type}


def _layout(body: str, title: str = "CoreServ Back Office") -> str:
    # Intentionally ugly: nested tables, no semantic tags, no data-testid.
    return f"""<html><head><title>{title}</title></head>
<body>
<table border="0" width="100%"><tr><td>
<table border="0"><tr><td><b>CoreServ</b> Legacy Servicing Console</td></tr></table>
</td></tr></table>
<hr>
{body}
</body></html>"""


@app.route("/", methods=["GET"])
def home():
    banner = ""
    if request.args.get("simulate_session_expired") == "1":
        banner = "<p style='color:red'>Your session has expired. Please log in again.</p>"
    body = f"""
{banner}
<table><tr><td>
<form action="/search" method="get">
<table><tr><td>Member ID:</td><td><input type="text" name="member_id"></td>
<td><input type="submit" value="Search"></td></tr></table>
</form>
</td></tr></table>
"""
    return _layout(body, "Home - Member Search")


@app.route("/search", methods=["GET"])
def search():
    member_id = (request.args.get("member_id") or "").strip()
    simulate_perm_denied = request.args.get("simulate_perm_denied") == "1"

    if simulate_perm_denied:
        body = "<table><tr><td><b>Access Denied</b>: you do not have permission to view this member.</td></tr></table>"
        return _layout(body, "Permission Denied"), 403

    member = MEMBERS.get(member_id)
    if not member:
        body = f"""<table><tr><td><b>No record found</b> for member ID '{member_id}'.</td></tr></table>
<p><a href="/">Back to search</a></p>"""
        return _layout(body, "Not Found")

    return redirect(url_for("member_detail", member_id=member_id))


@app.route("/member/<member_id>", methods=["GET"])
def member_detail(member_id):
    member = MEMBERS.get(member_id)
    if not member:
        return _layout("<p>No such member.</p>", "Not Found"), 404

    subs = SUBACCOUNTS.get(member_id, [])
    subs_rows = "".join(f"<tr><td>{s['id']}</td><td>{s['type']}</td></tr>" for s in subs) or "<tr><td colspan=2>None</td></tr>"

    # nested table layout, no data-testid, plain text labels only
    body = f"""
<table><tr><td>
  <table border="1"><tr><td><b>Member</b></td><td>{member['name']}</td></tr>
  <tr><td><b>Member ID</b></td><td>{member_id}</td></tr>
  <tr><td><b>Status</b></td><td>{member['status']}</td></tr>
  <tr><td><b>Savings Balance</b></td><td id="balance-cell">${member['savings_balance']}</td></tr>
  </table>
</td></tr></table>
<p><b>Sub-accounts</b></p>
<table border="1"><tr><td>ID</td><td>Type</td></tr>{subs_rows}</table>
<p><a href="/subaccount/new?member_id={member_id}">Open new sub-account</a> | <a href="/">New search</a></p>
"""
    return _layout(body, f"Member {member_id}")


@app.route("/subaccount/new", methods=["GET"])
def new_subaccount_form():
    member_id = request.args.get("member_id", "")
    if member_id not in MEMBERS:
        return _layout("<p>No such member.</p>", "Not Found"), 404
    body = f"""
<form action="/subaccount/create" method="post">
<input type="hidden" name="member_id" value="{member_id}">
<table><tr><td>Account type:</td><td>
  <select name="account_type">
    <option value="">-- select --</option>
    <option value="CHECKING">Checking</option>
    <option value="MONEY_MARKET">Money Market</option>
  </select>
</td></tr>
<tr><td>Initial deposit ($):</td><td><input type="text" name="initial_deposit"></td></tr>
<tr><td></td><td><input type="submit" value="Continue"></td></tr>
</table>
</form>
"""
    return _layout(body, "Open Sub-Account")


@app.route("/subaccount/create", methods=["POST"])
def create_subaccount():
    member_id = request.form.get("member_id", "")
    account_type = request.form.get("account_type", "")
    initial_deposit = request.form.get("initial_deposit", "")

    if member_id not in MEMBERS:
        return _layout("<p>No such member.</p>", "Not Found"), 404

    errors = []
    if not account_type:
        errors.append("Account type is required.")
    try:
        deposit_val = float(initial_deposit)
        if deposit_val < 0:
            errors.append("Initial deposit cannot be negative.")
    except ValueError:
        errors.append("Initial deposit must be a number.")

    if errors:
        err_html = "".join(f"<li>{e}</li>" for e in errors)
        body = f"""<table><tr><td><b>Validation error(s):</b><ul>{err_html}</ul></td></tr></table>
<p><a href="/subaccount/new?member_id={member_id}">Try again</a></p>"""
        return _layout(body, "Validation Error"), 422

    new_id = "".join(random.choices(string.digits, k=6))
    SUBACCOUNTS.setdefault(member_id, []).append({"id": new_id, "type": account_type})

    body = f"""
<table border="1"><tr><td><b>Confirmation</b></td></tr>
<tr><td>New {account_type.replace('_', ' ').title()} sub-account <b>{new_id}</b> opened for member {member_id} with initial deposit ${initial_deposit}.</td></tr>
</table>
<p><a href="/member/{member_id}">Back to member</a></p>
"""
    return _layout(body, "Sub-Account Confirmed")


if __name__ == "__main__":
    app.run(port=5055, debug=False)
