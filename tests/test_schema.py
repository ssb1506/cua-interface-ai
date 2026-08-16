import json

from agent.schema import Capability


def test_reference_artifacts_are_valid(tmp_path):
    for name in ("lookup_savings_balance", "open_subaccount"):
        path = f"artifacts/{name}.json"
        cap = Capability.model_validate_json(open(path).read())
        assert cap.capability_id == name
        assert cap.steps, "capability must have at least one step"
        assert cap.outcomes, "capability must declare at least one outcome"
        assert any(o.kind == "success" for o in cap.outcomes)


def test_artifact_roundtrips_through_json():
    cap = Capability.model_validate_json(open("artifacts/lookup_savings_balance.json").read())
    dumped = cap.model_dump_json()
    reloaded = Capability.model_validate_json(dumped)
    assert reloaded.capability_id == cap.capability_id
    assert len(reloaded.steps) == len(cap.steps)
