"""LLM-based risk scoring. Supports Gemini and Anthropic Claude."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from jinja2 import Template
from pydantic import BaseModel, Field, field_validator


# ============ Output schema ============

class Concern(BaseModel):
    severity: Literal["low", "medium", "high"]
    category: Literal["data", "security", "performance", "breaking-change", "ops", "logic", "other"]
    description: str


class RiskAssessment(BaseModel):
    risk_score: int = Field(ge=1, le=10)
    risk_label: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    summary: str
    concerns: list[Concern] = []
    auto_merge_recommendation: Literal["auto", "human-review", "block"]
    reasoning: str

    @field_validator("risk_label", mode="before")
    @classmethod
    def normalize_label(cls, v):
        if isinstance(v, str):
            return v.upper().strip()
        return v


# ============ Pricing (USD per 1M tokens, 2026) ============

PRICING = {
    "gemini-2.5-flash":   {"input": 0.30,  "output": 2.50},
    "gemini-2.5-pro":     {"input": 1.25,  "output": 10.0},
    "claude-sonnet-4-5":  {"input": 3.00,  "output": 15.0},
    "claude-haiku-4-5":   {"input": 1.00,  "output": 5.00},
    "claude-opus-4-6":    {"input": 15.00, "output": 75.0},
}


def calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return cost in USD."""
    # strip any provider prefix like "gemini/"
    key = model.split("/")[-1].lower()
    p = PRICING.get(key, {"input": 1.0, "output": 5.0})  # safe-ish default
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


# ============ Prompt loading ============

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "risk_score.md"


def render_prompt(**kwargs) -> str:
    template = Template(PROMPT_PATH.read_text(encoding="utf-8"))
    return template.render(**kwargs)


# ============ JSON extraction (LLMs sometimes wrap in markdown) ============

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def extract_json(text: str) -> dict:
    m = JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    # Find outermost braces
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"No JSON object in LLM response: {text[:200]}")
    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        # LLM JSON output is brittle: unescaped newlines, trailing commas,
        # truncation mid-token. Fall back to json-repair which handles all of these.
        try:
            from json_repair import repair_json
            repaired = repair_json(candidate, return_objects=True)
            if isinstance(repaired, dict) and repaired:
                return repaired
        except Exception:
            pass
        # Surface enough context for the action log to be useful
        print(f"[risk_scorer] JSON parse failed: {e}", flush=True)
        print(f"[risk_scorer] raw response (first 800 chars):\n{candidate[:800]}", flush=True)
        raise


# ============ Provider calls ============

def call_gemini(prompt: str, model: str, api_key: str) -> tuple[str, int, int]:
    """Returns (text, input_tokens, output_tokens)."""
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    # strip "gemini/" prefix if present (LiteLLM-style)
    model_name = model.split("/")[-1]
    m = genai.GenerativeModel(model_name)
    resp = m.generate_content(
        prompt,
        generation_config={
            "temperature": 0.2,
            "max_output_tokens": 8192,  # large PRs need headroom; Flash supports up to 65k
            "response_mime_type": "application/json",
        },
    )
    text = resp.text
    usage = resp.usage_metadata
    return text, usage.prompt_token_count, usage.candidates_token_count


def call_anthropic(prompt: str, model: str, api_key: str) -> tuple[str, int, int]:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    return text, msg.usage.input_tokens, msg.usage.output_tokens


# ============ Main entry ============

def score_risk(
    *,
    pr_title: str,
    pr_description: str,
    files_changed: list[str],
    diff_truncated: str,
    diff_was_truncated: bool,
    original_diff_lines: int,
    max_diff_lines: int,
    model: str,
    gemini_key: str = "",
    anthropic_key: str = "",
) -> tuple[RiskAssessment, float]:
    """Run risk scoring. Returns (assessment, cost_in_usd)."""
    prompt = render_prompt(
        pr_title=pr_title,
        pr_description=pr_description or "_(no description)_",
        files_changed=files_changed,
        files_changed_count=len(files_changed),
        diff_truncated=diff_truncated,
        diff_was_truncated=diff_was_truncated,
        original_diff_lines=original_diff_lines,
        max_diff_lines=max_diff_lines,
    )

    # Pick provider
    is_claude = model.lower().startswith("claude")
    if is_claude:
        if not anthropic_key:
            raise RuntimeError(f"Model {model} needs anthropic_api_key")
        text, in_tok, out_tok = call_anthropic(prompt, model, anthropic_key)
    else:
        if not gemini_key:
            raise RuntimeError(f"Model {model} needs gemini_api_key")
        text, in_tok, out_tok = call_gemini(prompt, model, gemini_key)

    cost = calc_cost(model, in_tok, out_tok)

    # Parse
    data = extract_json(text)
    assessment = RiskAssessment(**data)
    return assessment, cost
