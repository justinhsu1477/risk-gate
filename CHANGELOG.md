# Changelog

All notable changes to AI Risk Gate are documented here.
Versioning follows [SemVer](https://semver.org/).

## [1.2.0] — 2026-05-18

Three quality-of-life features inspired by patterns seen in
[tinyhumansai/openhuman](https://github.com/tinyhumansai/openhuman)'s
production CI setup.

### Added

- **`skip_labels` input** — comma-separated PR labels that exit cleanly
  without calling the LLM. Useful for `docs,chore,dependencies` PRs.
  Outputs `risk_label=SKIPPED`.
- **`mode: weekly_summary`** — pure-aggregation mode (no LLM call) that
  walks the last N days of PRs, finds risk-gate sticky comments, and
  opens or updates a `risk-gate-weekly` tracking issue with stats and the
  high-risk PR list. Drive with a cron schedule.
- **`summary_days` / `summary_repo` inputs** for the weekly summary mode.
- **`examples/weekly-summary.yml`** — ready-to-copy weekly cron workflow.

### Changed

- `examples/basic-workflow.yml` and `examples/gated-deploy.yml` now show
  the recommended `concurrency` block — cancels stale runs on new push.

## [1.0.2] — 2026-05-18

Bugfix for large PRs.

### Fixed

- **JSON parse failure on PRs with many concerns** — bumped Gemini
  `max_output_tokens` 4096 → 8192 (Flash supports up to 65k). Large PRs
  (37 files / 4000+ diff lines) were running out of output budget and
  truncating the JSON mid-token.
- **`json-repair` fallback** for the residual cases where the LLM emits
  unescaped newlines / trailing commas inside string values.
- Log first 800 chars of the raw response on parse failure to aid debug.

Symptom: `JSONDecodeError: Expecting ',' delimiter: line 10 column 6`
on [invest-pipeline#3](https://github.com/justinhsu1477/invest-pipeline/pull/3).

## [1.0.1] — 2026-05-18

Bugfixes discovered while wiring the action up to a real private repo
([justinhsu1477/invest-pipeline#2](https://github.com/justinhsu1477/invest-pipeline/pull/2)).
All three issues blocked the action from ever posting a comment; with these fixes
the end-to-end PR-comment flow works.

### Fixed

- **`action.yml` parse error** — the `github_token` description contained a
  literal `${{ secrets.GITHUB_TOKEN }}` which GitHub tried to evaluate as an
  expression at action load time. Replaced with plain prose.
- **Wrong workspace path inside Docker action** — `GITHUB_WORKSPACE` env var
  holds the *host* path (e.g. `/home/runner/work/repo/repo`) which doesn't
  exist inside the container; the repo is actually mounted at
  `/github/workspace`. Now detects the mount point.
- **`git` refused to operate on the mounted workspace** — git 2.35.2+ rejects
  cross-uid operations with `fatal: detected dubious ownership`. `git config
  --global --add safe.directory` in the Dockerfile didn't help because at
  runtime GitHub Actions sets `$HOME=/github/home`, bypassing `/root/.gitconfig`.
  Now adds `safe.directory` via `git config --system` at Python startup so it
  works regardless of `$HOME`.

### Internal

- Added safe.directory call inside the Dockerfile too (belt-and-suspenders).
- `examples/basic-workflow.yml`: added `timeout-minutes: 5` as a safety net.

## [1.0.0] — 2026-05-18

Initial public release.

### Added

- Docker-based GitHub Action that scores PR risk 1–10 using an LLM
- Sticky PR comment (upsert via `<!-- ai-risk-gate -->` marker)
- Supported models: `gemini-2.5-flash` (default), `gemini-2.5-pro`,
  `claude-sonnet-4-5`, `claude-haiku-4-5`
- Structured outputs for downstream jobs:
  `risk_score`, `risk_label`, `summary`, `concerns_json`, `cost_usd`
- Optional `fail_threshold` to hard-block merge above a risk level
- Customizable rubric via `prompts/risk_score.md` (fork-friendly)
- Example workflows: `basic-workflow.yml` (informational),
  `gated-deploy.yml` (route deploys by risk level)
- MIT licensed
