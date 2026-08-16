"""
Perception layer: converts the live Playwright page into a compact,
LLM-readable observation built from the accessibility tree rather than
raw HTML/DOM.

Why the accessibility tree instead of raw DOM or a screenshot-only approach:
- It's what a screen-reader / human operator effectively perceives:
  role + accessible name + value, independent of markup quality. Works
  identically whether the underlying markup is a clean React app or a
  1990s-style nested-table page with no test IDs.
- It's also the seam that lets this same interface extend to native
  desktop apps later (OS accessibility APIs expose the same role/name/value
  shape) - see REPORT.md Section 4.
- It's far more token-efficient than raw HTML for legacy markup (deeply
  nested tables balloon HTML size without adding signal).

We still keep a screenshot on hand for evidence/debugging, but the model's
decisions are driven off the accessibility snapshot, not pixels.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from playwright.sync_api import Page


@dataclass
class ObservedNode:
    role: str
    name: str
    ref_id: int  # index into the flat node list for this observation - stable within one observation only


@dataclass
class Observation:
    url: str
    title: str
    nodes: list[ObservedNode]

    def to_prompt_text(self) -> str:
        lines = [f"URL: {self.url}", f"Title: {self.title}", "Interactive/relevant elements:"]
        for n in self.nodes:
            lines.append(f"  [{n.ref_id}] role={n.role!r} name={n.name!r}")
        return "\n".join(lines)


# Roles worth surfacing to the model. Filters out generic/noise nodes so the
# accessibility tree of a nested-table legacy page doesn't drown the model
# in structural cruft.
_RELEVANT_ROLES = {
    "button", "link", "textbox", "combobox", "checkbox", "radio",
    "heading", "cell", "row", "table", "list", "listitem", "text",
}


def observe(page: Page, max_nodes: int = 80) -> Observation:
    snapshot = page.accessibility.snapshot(interesting_only=True) or {}
    nodes: list[ObservedNode] = []

    def walk(node: dict[str, Any]):
        if len(nodes) >= max_nodes:
            return
        role = node.get("role", "")
        name = (node.get("name") or "").strip()
        if role in _RELEVANT_ROLES and (name or role in {"textbox", "combobox"}):
            nodes.append(ObservedNode(role=role, name=name, ref_id=len(nodes)))
        for child in node.get("children", []) or []:
            walk(child)

    walk(snapshot)
    return Observation(url=page.url, title=page.title(), nodes=nodes)


def observation_to_dict(obs: Observation) -> dict:
    return {"url": obs.url, "title": obs.title, "nodes": [asdict(n) for n in obs.nodes]}
