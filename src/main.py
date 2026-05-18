"""
AI Risk Gate — GitHub Action entry point.

Reads PR context from GitHub event, calls LLM to score risk, posts comment.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from risk_scorer import RiskAssessment, score_risk
from pr_commenter import upsert_pr_comment


def gh_input(name: str, default: str = "") -> str:
    """Read GitHub Action input from env (composite key INPUT_<NAME>)."""
    return os.environ.get(f"INPUT_{name.upper()}", default)


def _get_workspace() -> str:
    """Find the repo workspace inside the Docker action container.

    In GitHub Docker actions, GITHUB_WORKSPACE env var holds the *host* path
    (e.g. /home/runner/work/repo/repo) which doesn't exist inside the container.
    The repo is actually mounted at /github/workspace. Prefer that.
    """
    if Path("/github/workspace/.git").exists():
        return "/github/workspace"
    ws = os.environ.get("GITHUB_WORKSPACE", "")
    if ws and Path(ws, ".git").exists():
        return ws
    return "."


def _trust_workspace() -> None:
    """Add safe.directory so git accepts the mounted workspace.

    Dockerfile-time `git config --global` writes to /root/.gitconfig but
    runtime $HOME is /github/home, so we add at runtime instead. Use
    --system so it works regardless of HOME.
    """
    paths = ["/github/workspace", os.environ.get("GITHUB_WORKSPACE", "")]
    for path in filter(None, paths):
        try:
            subprocess.run(
                ["git", "config", "--system", "--add", "safe.directory", path],
                capture_output=True, check=False,
            )
        except Exception:
            pass


def set_output(name: str, value: str) -> None:
    """Write to GITHUB_OUTPUT file (the modern way to set action outputs)."""
    out_file = os.environ.get("GITHUB_OUTPUT")
    if not out_file:
        print(f"[main] No GITHUB_OUTPUT set; printing: {name}={value}")
        return
    with open(out_file, "a", encoding="utf-8") as f:
        # multiline-safe delimiter syntax
        delim = f"EOF_{name.upper()}_{os.getpid()}"
        f.write(f"{name}<<{delim}\n{value}\n{delim}\n")


def load_event() -> dict:
    """Load the GitHub event payload."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not Path(path).exists():
        print("[main] No GITHUB_EVENT_PATH; running outside Actions context.")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_pr_diff(base_sha: str, head_sha: str) -> str:
    """Get diff between base and head. We're inside the checked-out repo."""
    try:
        result = subprocess.run(
            ["git", "diff", f"{base_sha}...{head_sha}"],
            capture_output=True, text=True, check=True,
            cwd=_get_workspace(),
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"[main] git diff failed: {e.stderr}", file=sys.stderr)
        # Fallback: diff HEAD against the base branch via fetch
        try:
            subprocess.run(["git", "fetch", "origin", base_sha], check=True,
                           cwd=_get_workspace())
            result = subprocess.run(
                ["git", "diff", f"origin/{base_sha}...HEAD"],
                capture_output=True, text=True, check=True,
                cwd=_get_workspace(),
            )
            return result.stdout
        except Exception as e2:
            print(f"[main] fallback diff also failed: {e2}", file=sys.stderr)
            return ""


def get_files_changed(base_sha: str, head_sha: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}...{head_sha}"],
            capture_output=True, text=True, check=True,
            cwd=_get_workspace(),
        )
        return [f for f in result.stdout.strip().split("\n") if f]
    except subprocess.CalledProcessError:
        return []


def truncate_diff(diff: str, max_lines: int) -> tuple[str, bool, int]:
    """Truncate diff to max_lines. Returns (truncated, was_truncated, original_lines)."""
    lines = diff.split("\n")
    original = len(lines)
    if original <= max_lines:
        return diff, False, original
    return "\n".join(lines[:max_lines]) + f"\n... [truncated {original - max_lines} more lines]", True, original


def main() -> int:
    _trust_workspace()

    # weekly_summary mode is an entirely different code path
    mode = gh_input("mode", "pr_review").lower().strip()
    if mode == "weekly_summary":
        from weekly_summary import run_weekly_summary
        return run_weekly_summary(
            gh_token=gh_input("github_token") or os.environ.get("GITHUB_TOKEN", ""),
            repo=gh_input("summary_repo") or os.environ.get("GITHUB_REPOSITORY", ""),
            days=int(gh_input("summary_days", "7")),
        )

    event = load_event()
    pr = event.get("pull_request", {})

    # Skip if PR has any label in skip_labels
    skip_labels_raw = gh_input("skip_labels", "").strip()
    if skip_labels_raw and pr:
        skip_set = {s.strip().lower() for s in skip_labels_raw.split(",") if s.strip()}
        pr_labels = {(l.get("name") or "").lower() for l in pr.get("labels", [])}
        hit = skip_set & pr_labels
        if hit:
            label = next(iter(hit))
            print(f"[main] PR labeled '{label}' is in skip_labels — exiting cleanly")
            set_output("risk_score", "0")
            set_output("risk_label", "SKIPPED")
            set_output("summary", f"Skipped: PR labeled '{label}'")
            set_output("concerns_json", "[]")
            set_output("cost_usd", "0.00")
            return 0

    if not pr:
        # Allow local testing via env vars
        pr_title = os.environ.get("LOCAL_PR_TITLE", "Local test PR")
        pr_description = os.environ.get("LOCAL_PR_DESCRIPTION", "")
        pr_number = int(os.environ.get("LOCAL_PR_NUMBER", "0"))
        base_sha = os.environ.get("LOCAL_BASE_SHA", "main")
        head_sha = os.environ.get("LOCAL_HEAD_SHA", "HEAD")
        repo_full = os.environ.get("LOCAL_REPO", "")
    else:
        pr_title = pr.get("title", "")
        pr_description = pr.get("body", "") or ""
        pr_number = pr.get("number", 0)
        base_sha = pr.get("base", {}).get("sha", "")
        head_sha = pr.get("head", {}).get("sha", "")
        repo_full = event.get("repository", {}).get("full_name", "")

    print(f"[main] PR #{pr_number}: {pr_title}")
    print(f"[main] {repo_full} | {base_sha[:7]}...{head_sha[:7]}")

    # Gather diff + files
    diff = get_pr_diff(base_sha, head_sha)
    files = get_files_changed(base_sha, head_sha)
    print(f"[main] {len(files)} files changed, {len(diff.splitlines())} diff lines")

    if not diff:
        print("[main] Empty diff — nothing to score. Exiting cleanly.")
        set_output("risk_score", "0")
        set_output("risk_label", "EMPTY")
        set_output("summary", "No diff detected")
        set_output("concerns_json", "[]")
        set_output("cost_usd", "0.00")
        return 0

    max_diff_lines = int(gh_input("max_diff_lines", "3000"))
    diff_trunc, was_truncated, original_lines = truncate_diff(diff, max_diff_lines)

    # Score
    model = gh_input("model", "gemini-2.5-flash")
    gemini_key = gh_input("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    anthropic_key = gh_input("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    if not gemini_key and not anthropic_key:
        print("[main] ERROR: must provide gemini_api_key or anthropic_api_key", file=sys.stderr)
        return 2

    try:
        assessment, cost_usd = score_risk(
            pr_title=pr_title,
            pr_description=pr_description,
            files_changed=files,
            diff_truncated=diff_trunc,
            diff_was_truncated=was_truncated,
            original_diff_lines=original_lines,
            max_diff_lines=max_diff_lines,
            model=model,
            gemini_key=gemini_key,
            anthropic_key=anthropic_key,
        )
    except Exception as e:
        print(f"[main] Risk scoring failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 3

    print(f"[main] Score: {assessment.risk_score} ({assessment.risk_label})")
    print(f"[main] Summary: {assessment.summary}")
    print(f"[main] Cost: ${cost_usd:.4f}")

    # Comment on PR (if we have token + pr_number)
    gh_token = gh_input("github_token") or os.environ.get("GITHUB_TOKEN", "")
    if gh_token and pr_number and repo_full:
        try:
            upsert_pr_comment(
                token=gh_token,
                repo=repo_full,
                pr_number=pr_number,
                assessment=assessment,
                model=model,
                cost_usd=cost_usd,
            )
            print(f"[main] PR comment posted to {repo_full}#{pr_number}")
        except Exception as e:
            print(f"[main] PR comment failed (continuing): {e}", file=sys.stderr)
    else:
        print("[main] No github_token / pr_number — skipping comment")

    # Set outputs
    set_output("risk_score", str(assessment.risk_score))
    set_output("risk_label", assessment.risk_label)
    set_output("summary", assessment.summary)
    set_output("concerns_json", json.dumps([c.model_dump() for c in assessment.concerns]))
    set_output("cost_usd", f"{cost_usd:.4f}")

    # Optional: fail the action if score >= fail_threshold
    fail_threshold = int(gh_input("fail_threshold", "0"))
    if fail_threshold > 0 and assessment.risk_score >= fail_threshold:
        print(f"[main] Risk score {assessment.risk_score} >= threshold {fail_threshold} — failing action")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
