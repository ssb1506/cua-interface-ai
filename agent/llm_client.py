"""
Thin LLM client for the discovery loop.

Default provider: Google Gemini, via the free-tier Gemini API (no payment
method required) - set GEMINI_API_KEY. Forced function-calling keeps the
model's decision at every turn to a single structured action, same as the
original Anthropic-based design - we're not parsing prose, we're getting a
typed action directly.

To switch back to Anthropic (e.g. if you have Claude API credits instead),
set CUA_PROVIDER=anthropic and ANTHROPIC_API_KEY - the AnthropicLLMClient
below is preserved and behaves identically to before.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

PROVIDER = os.environ.get("CUA_PROVIDER", "gemini")

SYSTEM_PROMPT = """You are operating a legacy bank back-office web application on behalf of an \
automation-recording system. You will be given a GOAL and, each turn, an OBSERVATION of the \
current page expressed as accessibility-tree nodes (role + accessible name), because the \
underlying markup has no stable test IDs.

Rules:
- Call the `act` function exactly once per turn with exactly one action.
- Prefer the most direct path to the goal. Do not explore or click things unrelated to the goal.
- Use action=extract to read a value (e.g. a balance) into a named output field when the goal \
asks you to read/report something.
- Use action=done once the goal is fully achieved, naming the outcome reached.
- Use action=stuck if you cannot find a way to proceed safely or the page indicates a state you \
don't know how to handle - do not guess wildly or take irreversible actions you're unsure about.
- Only reference elements that appear in the current OBSERVATION by their role and name.
"""

_ACT_PARAMETERS = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string", "description": "One sentence: why this action, given the goal and observation."},
        "action": {
            "type": "string",
            "enum": ["navigate", "click", "fill", "select", "extract", "done", "stuck"],
        },
        "url": {"type": "string", "description": "For action=navigate."},
        "role": {"type": "string", "description": "Accessibility role of the target element, for click/fill/select/extract."},
        "name": {"type": "string", "description": "Accessible name of the target element, for click/fill/select/extract."},
        "value": {"type": "string", "description": "For fill/select: the value to enter. For extract: the output field name to store the read value under."},
        "outcome": {"type": "string", "description": "For action=done: which named outcome was reached (e.g. 'success' or 'member_not_found')."},
        "reason": {"type": "string", "description": "For action=stuck: why the agent cannot safely proceed."},
    },
    "required": ["reasoning", "action"],
}


def _to_gemini_schema(schema: dict) -> dict:
    """Gemini's function-calling schema requires JSON-Schema-like dicts but
    with UPPERCASE type names (STRING/OBJECT/...) instead of standard
    lowercase JSON Schema. Converts recursively so we keep one canonical
    (lowercase, standard) schema and derive Gemini's variant from it."""
    out = dict(schema)
    if "type" in out and isinstance(out["type"], str):
        out["type"] = out["type"].upper()
    if "properties" in out:
        out["properties"] = {k: _to_gemini_schema(v) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = _to_gemini_schema(out["items"])
    return out


_ACT_PARAMETERS_GEMINI = _to_gemini_schema(_ACT_PARAMETERS)


class GeminiLLMClient:
    """Default: free-tier Gemini API, forced function calling."""

    def __init__(self):
        from google import genai
        from google.genai import types

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com "
                "(Get API key -> Create API key - no payment method required for the free tier)."
            )
        self._types = types
        self.client = genai.Client(api_key=api_key)
        self.model = os.environ.get("CUA_MODEL", "gemini-2.5-flash")
        self._act_declaration = types.FunctionDeclaration(
            name="act",
            description="Choose exactly one action to take against the current page, based on the observation.",
            parameters=_ACT_PARAMETERS_GEMINI,
        )
        self._tool = types.Tool(function_declarations=[self._act_declaration])

    def decide(self, goal: str, observation_text: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        types = self._types
        # translate plain {"role": "user"/"assistant", "text": ...} history into Gemini's
        # Content format (role must be "user" or "model", never "assistant").
        contents: list[Any] = []
        for turn in history:
            role = "model" if turn["role"] == "assistant" else "user"
            contents.append(types.Content(role=role, parts=[types.Part(text=turn["text"])]))
        contents.append(
            types.Content(role="user", parts=[types.Part(
                text=f"GOAL: {goal}\n\nOBSERVATION:\n{observation_text}\n\nChoose your next action."
            )])
        )

        max_retries = 6
        for attempt in range(max_retries):
            try:
                resp = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        tools=[self._tool],
                        tool_config=types.ToolConfig(
                            function_calling_config=types.FunctionCallingConfig(mode="ANY", allowed_function_names=["act"])
                        ),
                    ),
                )
                break
            except Exception as e:  # noqa: BLE001 - free-tier rate limiting, retry with backoff
                msg = str(e)
                if "RESOURCE_EXHAUSTED" not in msg and "429" not in msg:
                    raise
                if attempt == max_retries - 1:
                    raise
                # honor the server's suggested retryDelay if present, else back off progressively
                m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)", msg)
                wait_s = int(m.group(1)) + 2 if m else min(15 * (attempt + 1), 65)
                print(f"[llm] Gemini free-tier rate limit hit - waiting {wait_s}s before retry ({attempt + 1}/{max_retries})...")
                time.sleep(wait_s)

        for part in resp.candidates[0].content.parts:
            if part.function_call is not None and part.function_call.name == "act":
                return dict(part.function_call.args)
        raise RuntimeError(f"Model did not return a function call: {resp}")


class AnthropicLLMClient:
    """Original provider - kept for anyone with Claude API credits instead."""

    def __init__(self):
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = os.environ.get("CUA_MODEL", "claude-sonnet-4-5")
        self._tool = {
            "name": "act",
            "description": "Choose exactly one action to take against the current page, based on the observation.",
            "input_schema": _ACT_PARAMETERS,
        }

    def decide(self, goal: str, observation_text: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        # history is already plain {"role": "user"/"assistant", "text": ...} - Anthropic's
        # messages format accepts that shape directly (content as a plain string).
        messages = [{"role": h["role"], "content": h["text"]} for h in history] + [
            {"role": "user", "content": f"GOAL: {goal}\n\nOBSERVATION:\n{observation_text}\n\nChoose your next action."}
        ]
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            tools=[self._tool],
            tool_choice={"type": "tool", "name": "act"},
            messages=messages,
        )
        for block in resp.content:
            if block.type == "tool_use":
                return block.input
        raise RuntimeError(f"Model did not return a tool_use block: {resp.content}")


def LLMClient():
    """Factory - returns the provider selected by CUA_PROVIDER (default: gemini, free tier)."""
    if PROVIDER == "anthropic":
        return AnthropicLLMClient()
    return GeminiLLMClient()
