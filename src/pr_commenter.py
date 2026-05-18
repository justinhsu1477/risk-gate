"""Post a sticky comment on the PR. Updates existing comment if found."""
from __future__ import annotations

import requests

from risk_scorer import RiskAssessment

# Marker to identify our comments for upsert behavior
MARKER = "<!-- ai-risk-gate -->"


def format_comment(assessment: RiskAssessment, model: str, cost_usd: float) -> str:
    """Render the PR comment markdown."""
    # Emoji per risk label
    emoji = {
        "LOW": "🟢",
        "MEDIUM": "🟡",
        "HIGH": "🟠",
        "CRITICAL": "🔴",
    }.get(assessment.risk_label, "⚪")

    # Score bar (visual)
    filled = "█" * assessment.risk_score
    empty = "░" * (10 - assessment.risk_score)

    # Auto-merge badge
    badge_map = {
        "auto":         "✅ **Safe to auto-merge**",
        "human-review": "👀 **Human review recommended**",
        "block":        "🛑 **Should NOT merge without thorough review**",
    }
    badge = badge_map.get(assessment.auto_merge_recommendation, "")

    parts = [
        MARKER,
        f"## {emoji} AI Risk Gate — Score: {assessment.risk_score}/10 ({assessment.risk_label})",
        "",
        f"`{filled}{empty}` {assessment.risk_score}/10",
        "",
        f"**{assessment.summary}**",
        "",
        badge,
        "",
        f"### Reasoning",
        assessment.reasoning,
    ]

    if assessment.concerns:
        parts += ["", "### Concerns", ""]
        # Sort high → low severity
        sev_order = {"high": 0, "medium": 1, "low": 2}
        sorted_c = sorted(assessment.concerns, key=lambda c: sev_order.get(c.severity, 99))
        for c in sorted_c:
            sev_icon = {"high": "🔥", "medium": "⚠️", "low": "💡"}.get(c.severity, "•")
            parts.append(f"- {sev_icon} **[{c.category}]** {c.description}")
    else:
        parts += ["", "_No specific concerns._"]

    parts += [
        "",
        "---",
        f"<sub>Model: `{model}` · Cost: `${cost_usd:.4f}` · "
        f"[Configure](https://github.com/justinhsu1477/risk-gate)</sub>",
    ]

    return "\n".join(parts)


def find_existing_comment(token: str, repo: str, pr_number: int) -> int | None:
    """Find existing risk-gate comment ID, or None."""
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    for c in r.json():
        if MARKER in (c.get("body") or ""):
            return c["id"]
    return None


def upsert_pr_comment(
    *,
    token: str,
    repo: str,
    pr_number: int,
    assessment: RiskAssessment,
    model: str,
    cost_usd: float,
) -> None:
    """Update existing risk-gate comment or post new one."""
    body = format_comment(assessment, model, cost_usd)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    existing_id = find_existing_comment(token, repo, pr_number)
    if existing_id:
        url = f"https://api.github.com/repos/{repo}/issues/comments/{existing_id}"
        r = requests.patch(url, headers=headers, json={"body": body}, timeout=15)
    else:
        url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
        r = requests.post(url, headers=headers, json={"body": body}, timeout=15)

    r.raise_for_status()
