"""
The capability artifact schema.

This is the contract between:
  - the discovery run (LLM) that produces it,
  - a human reviewer who approves it,
  - and the replay engine / calling AI agent that invokes it in production.

Design decisions (see REPORT.md for full rationale):

- Locators are a *ranked fallback chain*, never a single selector. Each
  locator strategy is tagged with a `kind` so the replay engine knows how
  to resolve it (role/name is preferred -> text -> css as last resort).
  This is the direct answer to "no clean DOM, no test IDs."
- `outcomes` is a first-class list separate from `steps`. A step can lead to
  a named business outcome (e.g. "member_not_found") instead of a hard
  failure. This is what keeps the replay contract from conflating "no such
  member" with a crash.
- `checkpoint` on every step (not just at the end) - every action's success
  is verified before moving on, so replay fails fast at the exact step,
  with expected/observed detail, rather than plowing ahead on a stale page.
- Parameters and outputs are typed and named, independent of the step
  sequence, so a calling agent has a clean function-call-like contract
  without needing to understand the recorded steps at all.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class LocatorKind(str, Enum):
    ROLE_NAME = "role_name"      # Playwright get_by_role(role, name=...) - preferred
    LABEL_TEXT = "label_text"    # get_by_label / nearby text match
    TEXT = "text"                # get_by_text - substring/exact
    CSS = "css"                  # raw CSS selector - last resort, flagged as brittle


class Locator(BaseModel):
    kind: LocatorKind
    value: str
    role: Optional[str] = None          # for ROLE_NAME, e.g. "button", "textbox"
    brittle: bool = False               # true for CSS fallbacks - surfaced to reviewers


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    EXTRACT = "extract"          # read a value into the output bag
    WAIT_FOR = "wait_for"        # explicit wait on a condition, not a fixed sleep


class RiskLevel(str, Enum):
    SAFE = "safe"                # read-only / reversible (search, navigate, extract)
    RISKY = "risky"              # irreversible or state-mutating (submit, create, delete)


class Checkpoint(BaseModel):
    """Condition asserted after a step to confirm it actually worked."""
    description: str
    locator: Optional[Locator] = None
    expect: Literal["visible", "text_contains", "url_matches"] = "visible"
    expect_value: Optional[str] = None


class Step(BaseModel):
    step_id: str
    action: ActionType
    risk: RiskLevel
    locator: list[Locator] = Field(default_factory=list, description="Ranked fallback chain, primary first.")
    value_ref: Optional[str] = Field(
        default=None, description="Name of an input param or literal to type/select. Never a raw secret."
    )
    literal_value: Optional[str] = None
    checkpoint: Optional[Checkpoint] = None
    extract_as: Optional[str] = Field(default=None, description="If action=EXTRACT, output field name.")
    notes: Optional[str] = None


class Outcome(BaseModel):
    """A named, expected result of running this capability - success or a
    legitimate business result the caller needs to branch on."""
    name: str
    kind: Literal["success", "business_outcome"]
    description: str
    detection: Checkpoint


class InputParam(BaseModel):
    name: str
    type: Literal["string", "number", "boolean"]
    required: bool = True
    description: str = ""


class OutputField(BaseModel):
    name: str
    type: Literal["string", "number", "boolean"]
    description: str = ""


class Capability(BaseModel):
    model_config = {"use_enum_values": True}
    """A versioned, reviewable, agent-invocable automation artifact."""
    capability_id: str
    version: str = "1.0.0"
    description: str
    target: dict[str, Any] = Field(
        description="Scope this capability is allowed to run against, e.g. "
                     "{'allowed_domains': ['localhost:5055'], 'app': 'coreserv-legacy'}"
    )
    input_params: list[InputParam]
    outputs: list[OutputField]
    steps: list[Step]
    outcomes: list[Outcome]
    approval_state: Literal["draft", "approved"] = "draft"
    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description="How this was produced: discovery run id, model, timestamp. No raw transcript, no secrets.",
    )
