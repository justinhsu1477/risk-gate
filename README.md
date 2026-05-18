# AI Risk Gate

GitHub Action that uses an LLM to score the **deployment risk** of every pull request from 1–10, and posts a sticky comment with the analysis. Works as a soft signal (informational) or a hard gate (failing the workflow above a threshold).

```
🟠 AI Risk Gate — Score: 7/10 (HIGH)

████████░░ 7/10

Schema migration drops a non-nullable column; downstream consumers may fail.

👀 Human review recommended

### Concerns
- 🔥 [data] migration 0042: column `users.legacy_id` dropped without read-validation
- ⚠️ [breaking-change] public method `User.get_legacy_id()` removed (services A, B still call)
- 💡 [ops] no rollback plan in PR description
```

---

## Why

- **CodeRabbit** does review comments, line by line. Useful for nits and bugs.
- **This** does one thing well: a single **risk number** + **decision recommendation** you can branch your CD on.
- Pairs nicely with CodeRabbit / PR-Agent — they review the code, this gates the deploy.

## Features

- LLM: **Gemini 2.5 Flash** (default, cheap), **Gemini 2.5 Pro**, or **Claude Sonnet/Haiku 4.5**
- Sticky comment on the PR (updates instead of spamming)
- Structured outputs: `risk_score`, `risk_label`, `summary`, `concerns_json`, `cost_usd`
- Optional fail-on-threshold to block merge

## Usage

```yaml
# .github/workflows/risk-gate.yml
name: AI Risk Gate

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write

jobs:
  risk:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # need full history for diff

      - uses: justinhsu1477/risk-gate@v1
        with:
          github_token:   ${{ secrets.GITHUB_TOKEN }}
          gemini_api_key: ${{ secrets.GEMINI_API_KEY }}
          model: gemini-2.5-flash
          fail_threshold: 0   # never fail (informational only)
```

### Hard gate (block high-risk PRs)

```yaml
fail_threshold: 8   # action fails if score >= 8 → blocks merge if branch protection requires this check
```

### Branch on the score in downstream jobs

```yaml
jobs:
  risk:
    runs-on: ubuntu-latest
    outputs:
      score: ${{ steps.gate.outputs.risk_score }}
      rec:   ${{ steps.gate.outputs.risk_label }}
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - id: gate
        uses: justinhsu1477/risk-gate@v1
        with:
          github_token:   ${{ secrets.GITHUB_TOKEN }}
          gemini_api_key: ${{ secrets.GEMINI_API_KEY }}

  auto-deploy:
    needs: risk
    if: needs.risk.outputs.score <= 3
    runs-on: ubuntu-latest
    steps:
      - run: echo "Low risk — auto-deploying"
      # ... your deploy steps ...

  require-approval:
    needs: risk
    if: needs.risk.outputs.score > 3 && needs.risk.outputs.score < 8
    runs-on: ubuntu-latest
    environment: production   # GitHub will require approval
    steps:
      - run: echo "Medium risk — waiting for human approval"
```

## Inputs

| Name | Required | Default | Description |
|------|----------|---------|-------------|
| `github_token` | ✅ | — | For posting PR comments. Use `${{ secrets.GITHUB_TOKEN }}` |
| `gemini_api_key` | ⚠️ one of | — | Required if using a Gemini model |
| `anthropic_api_key` | ⚠️ one of | — | Required if using a Claude model |
| `model` | | `gemini-2.5-flash` | `gemini-2.5-flash`, `gemini-2.5-pro`, `claude-sonnet-4-5`, `claude-haiku-4-5` |
| `fail_threshold` | | `0` | Action fails if score ≥ this (0 = never fail) |
| `max_diff_lines` | | `3000` | Truncate diff to control cost |
| `skip_labels` | | `''` | Comma-separated labels that skip the check (`docs,chore,dependencies`) |
| `mode` | | `pr_review` | `pr_review` (default) or `weekly_summary` |
| `summary_days` | | `7` | Lookback days for `weekly_summary` mode |
| `summary_repo` | | (current repo) | Override repo for `weekly_summary` mode |

## Outputs

| Name | Example |
|------|---------|
| `risk_score` | `7` |
| `risk_label` | `HIGH` |
| `summary` | `Schema migration drops non-nullable column` |
| `concerns_json` | `[{"severity":"high","category":"data",...}]` |
| `cost_usd` | `0.0234` |

## Cost

| Model | ~Cost per PR* |
|-------|--------------|
| `gemini-2.5-flash` | **$0.005–0.05** |
| `gemini-2.5-pro` | $0.02–0.20 |
| `claude-haiku-4-5` | $0.02–0.10 |
| `claude-sonnet-4-5` | $0.05–0.30 |

*Depends on PR size. Diff is truncated to `max_diff_lines` (default 3000) to bound the worst case.

## Rubric

The model scores using this rubric (full details in [prompts/risk_score.md](prompts/risk_score.md)):

| Score | Label | Examples |
|-------|-------|----------|
| 1-3 | LOW | Typos, docstrings, formatting, tests-only |
| 4-6 | MEDIUM | New feature, bug fix touching ≤3 files |
| 7-8 | HIGH | Schema migration, auth changes, multi-file refactor |
| 9-10 | CRITICAL | Drops table, exposes secrets, IAM changes |

## Skip docs / chore PRs

Don't waste API cost on cosmetic PRs:

```yaml
- uses: justinhsu1477/risk-gate@v1
  with:
    github_token:   ${{ secrets.GITHUB_TOKEN }}
    gemini_api_key: ${{ secrets.GEMINI_API_KEY }}
    skip_labels: docs,chore,dependencies
```

PRs with any of those labels exit cleanly with `risk_label=SKIPPED`.

## Cancel stale runs

Add `concurrency` to your workflow so pushing a new commit cancels the
in-progress scoring of the previous one:

```yaml
concurrency:
  group: risk-gate-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
```

## Weekly summary mode

Pair the per-PR workflow with a weekly aggregation that walks the last 7 days of
PRs, finds the risk-gate sticky comments, and opens a tracking issue with the
stats and high-risk list. **No LLM calls** — pure aggregation, so it's free.

```yaml
# .github/workflows/risk-gate-weekly.yml
name: AI Risk Gate Weekly Summary

on:
  schedule:
    - cron: "0 6 * * 1"   # Monday 06:00 UTC
  workflow_dispatch:

permissions:
  contents: read
  issues: write
  pull-requests: read

jobs:
  summary:
    runs-on: ubuntu-latest
    steps:
      - uses: justinhsu1477/risk-gate@v1
        with:
          mode: weekly_summary
          github_token: ${{ secrets.GITHUB_TOKEN }}
          summary_days: 7
```

Output: a markdown issue labeled `risk-gate-weekly` showing total PRs,
average score, distribution, and high-risk PR list. Updates in place each week.

## Customize the rubric

The prompt lives in [`prompts/risk_score.md`](prompts/risk_score.md). Fork this repo, edit the prompt to add your team's red flags, and use your fork:

```yaml
uses: your-org/risk-gate@v1
```

## Local development

```bash
cd risk-gate
docker build -t risk-gate .

# Test locally against a real PR (no GitHub Actions context)
docker run --rm \
  -e GEMINI_API_KEY=$GEMINI_API_KEY \
  -e GITHUB_TOKEN=$GH_TOKEN \
  -e LOCAL_REPO=justinhsu1477/some-repo \
  -e LOCAL_PR_NUMBER=42 \
  -e LOCAL_PR_TITLE="Add date filter to sql.py" \
  -e LOCAL_BASE_SHA=main \
  -e LOCAL_HEAD_SHA=HEAD \
  -v /path/to/cloned/repo:/github/workspace \
  -e GITHUB_WORKSPACE=/github/workspace \
  risk-gate
```

## License

MIT.

## Related projects

- [CodeRabbit](https://coderabbit.ai) — line-by-line PR review (we recommend running both)
- [PR-Agent](https://github.com/Codium-ai/pr-agent) — `/review`, `/improve`, `/test` commands
- [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) — autonomous bug-fix agent for issue-to-PR
