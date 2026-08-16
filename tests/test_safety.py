from agent.safety import AllowlistPolicy, SafetyGate, GuardrailViolation, classify_risk, redact, redact_dict


def test_allowlist_blocks_offdomain():
    policy = AllowlistPolicy()
    ok, _ = policy.check_url("http://evil.example.com/search")
    assert not ok


def test_allowlist_allows_indomain_route():
    policy = AllowlistPolicy()
    ok, _ = policy.check_url("http://localhost:5055/search?member_id=1")
    assert ok


def test_action_allowlist():
    policy = AllowlistPolicy()
    ok, _ = policy.check_action("delete_everything")
    assert not ok
    ok, _ = policy.check_action("click")
    assert ok


def test_risk_classification():
    assert classify_risk("/subaccount/create") == "risky"
    assert classify_risk("/member/12345") == "safe"


def test_safety_gate_blocks_risky_by_default():
    gate = SafetyGate(AllowlistPolicy(), allow_risky=False)
    try:
        gate.check_risk("/subaccount/create")
        assert False, "should have raised"
    except GuardrailViolation:
        pass


def test_safety_gate_allows_risky_when_flagged():
    gate = SafetyGate(AllowlistPolicy(), allow_risky=True)
    assert gate.check_risk("/subaccount/create") == "risky"


def test_redaction():
    assert redact("password", "hunter2") == "[REDACTED]"
    assert redact("member_id", "12345") == "12345"
    d = redact_dict({"api_key": "sk-abc", "member_id": "12345"})
    assert d["api_key"] == "[REDACTED]"
    assert d["member_id"] == "12345"
