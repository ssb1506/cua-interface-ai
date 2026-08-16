"""
Safety & policy guardrails.

- Allowlist: explicit domains/routes + action types the agent may act on.
  Enforced on every navigate/click/fill during discovery AND on every
  step during replay - not just at recording time.
- Risk classification: RISKY (state-mutating / irreversible) actions
  require an explicit `allow_risky=True` flag from the caller to execute
  unattended; otherwise they pause for human confirmation. This is the
  conservative default called for in section 3.4.
- Redaction: a small denylist of field-name patterns that must never be
  written to artifacts or logs verbatim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class AllowlistPolicy:
    allowed_domains: list[str] = field(default_factory=lambda: ["localhost:5055", "127.0.0.1:5055"])
    allowed_routes: list[str] = field(
        default_factory=lambda: ["/", "/search", "/member/", "/subaccount/new", "/subaccount/create"]
    )
    allowed_actions: list[str] = field(
        default_factory=lambda: ["navigate", "click", "fill", "select", "extract", "wait_for"]
    )

    def check_url(self, url: str) -> tuple[bool, str]:
        parsed = urlparse(url)
        netloc = parsed.netloc or parsed.path  # tolerate bare host:port
        if not any(netloc.startswith(d) for d in self.allowed_domains):
            return False, f"domain '{netloc}' not in allowlist {self.allowed_domains}"
        if self.allowed_routes and not any(parsed.path.startswith(r) for r in self.allowed_routes):
            return False, f"route '{parsed.path}' not in allowlist {self.allowed_routes}"
        return True, ""

    def check_action(self, action: str) -> tuple[bool, str]:
        if action not in self.allowed_actions:
            return False, f"action '{action}' not in allowlist {self.allowed_actions}"
        return True, ""


# Action types considered irreversible / state-mutating against this target.
# (In the schema these map 1:1 to Step.risk == RiskLevel.RISKY.)
RISKY_ROUTES = {"/subaccount/create"}


def classify_risk(url_or_route: str) -> str:
    path = urlparse(url_or_route).path or url_or_route
    return "risky" if any(path.startswith(r) for r in RISKY_ROUTES) else "safe"


_SENSITIVE_FIELD_PATTERNS = [
    re.compile(p, re.I)
    for p in [r"password", r"ssn", r"social.?security", r"card.?number", r"cvv", r"api[_-]?key", r"token", r"secret"]
]


def redact(field_name: str, value: str) -> str:
    """Redact a value if its field name looks sensitive. Applied before
    anything is written into artifacts, logs, or evidence."""
    if any(p.search(field_name) for p in _SENSITIVE_FIELD_PATTERNS):
        return "[REDACTED]"
    return value


def redact_dict(d: dict) -> dict:
    return {k: redact(k, str(v)) for k, v in d.items()}


class GuardrailViolation(Exception):
    pass


class SafetyGate:
    """Central checkpoint every action - discovery or replay - passes
    through before it touches the live surface."""

    def __init__(self, policy: AllowlistPolicy, allow_risky: bool = False):
        self.policy = policy
        self.allow_risky = allow_risky

    def check(self, action: str, target_url: str | None = None) -> None:
        ok, reason = self.policy.check_action(action)
        if not ok:
            raise GuardrailViolation(reason)
        if target_url:
            ok, reason = self.policy.check_url(target_url)
            if not ok:
                raise GuardrailViolation(reason)

    def check_risk(self, route: str) -> str:
        """Returns 'safe' or 'risky'. Raises if risky and not allowed
        (caller should route to escalation instead of catching this as a hard error)."""
        risk = classify_risk(route)
        if risk == "risky" and not self.allow_risky:
            raise GuardrailViolation(f"risky action on '{route}' requires human confirmation (allow_risky=False)")
        return risk
