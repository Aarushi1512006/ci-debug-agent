# Auto-Debugging & Refactoring CI/CD Agent

LangChain + Mistral + ChromaDB agent that watches CI test runs, diagnoses
failures with RAG over the repo, proposes a minimal fix, verifies it by
re-running tests on a throwaway branch, and opens a PR only if it's proven
to pass.

**Current scope:** Python/pytest only. Refactoring and multi-language support are planned.

## Prerequisites

- Python 3.8+
- GitHub account with a target repository
- Mistral API key
- GitHub token (with `repo` and `workflow` scopes)

## How it fits together

```
GitHub Action (on push/PR)
   -> runs pytest, captures log
   -> POSTs log to FastAPI agent  (agent/main.py: /debug/from-log)
        -> context.py    parses traceback, extracts failing function via ast
        -> indexer.py    retrieves related code from ChromaDB (RAG)
        -> chains.py     Mistral proposes a structured FixResponse
        -> patcher.py    applies patch on agent-fix/* branch, re-runs pytest
        -> github_client.py  opens PR if verified, else retries (max 2x) or bails
```

### Decision flow:
1. **Traceback parsed successfully?** → extract failing function via AST
2. **Related code found in RAG?** → Mistral proposes a fix
3. **Patch applies cleanly?** → re-run test suite on throwaway branch
4. **Tests pass?** → open PR; otherwise retry (max 2 attempts)
5. **All retries exhausted?** → post comment on issue/PR explaining failure

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/Aarushi1512006/ci-debug-agent.git
cd ci-debug-agent
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `MISTRAL_API_KEY` | API key from [Mistral Console](https://console.mistral.ai) |
| `GITHUB_TOKEN` | Personal access token with `repo` and `workflow` scopes |
| `GITHUB_REPO` | Target repository in `owner/repo` format (e.g., `myorg/myapp`) |
| `AGENT_URL` | URL where this service is deployed (for CI to POST logs) |

### 3. Index your target repository

Run this once before the first CI run (or after major refactors):

```bash
python -m agent.indexer --repo /path/to/target/repo
```

Or via the HTTP endpoint:

```bash
curl -X POST "http://localhost:8000/index" \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/target/repo"}'
```

For incremental updates after code changes:

```bash
curl -X POST "http://localhost:8000/index" \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/target/repo", "changed_files": ["src/main.py", "tests/test_main.py"]}'
```

### 4. Run the service locally

```bash
uvicorn agent.main:app --reload --port 8000
```

The agent will be available at `http://localhost:8000`.

### 5. Integrate with your CI

In your target repository:

1. Add `AGENT_URL` as a [repository secret](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions) (e.g., `https://my-agent.example.com`)
2. Copy [`.github/workflows/agent-debug.yml`](`.github/workflows/agent-debug.yml`) to your repo
3. Push to trigger the workflow on test failures

## Smoke test (no API keys needed)

Validates the traceback parser and AST function extraction before testing with the LLM:

```bash
python -m pytest tests/test_context.py -v
```

Expected output: All parsing tests pass ✓

To run the full test suite:

```bash
python -m pytest tests/ -v
```

## Guardrails (agent/config.py)

These safety limits prevent accidental commits or destructive changes:

- **Max 2 fix attempts** per failure before posting a comment and bailing
- **Protected paths:** Never modifies `.github/workflows/`, `.env`, `secrets/`, or config files
- **Python only:** Only edits `.py` files
- **Exact match patching:** The LLM's `original_snippet` must match the file *exactly*; fuzzy matching is disabled to avoid silent corruption
- **Disposable testing:** Every fix is re-verified on an ephemeral `agent-fix/*` branch before opening a PR; nothing is pushed to `main` directly
- **No direct commits:** All changes are proposed via PR for human review

## Troubleshooting

### "Traceback parser failed"

**Cause:** The test output didn't include a recognizable Python traceback.

**Fix:** Ensure pytest is configured to output full tracebacks. Add to `pytest.ini`:
```ini
[pytest]
addopts = --tb=long
```

### "No related code found in ChromaDB"

**Cause:** The index is stale or incomplete.

**Fix:** Re-index the repo:
```bash
curl -X POST "http://localhost:8000/index" \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/target/repo", "force_reindex": true}'
```

### "Patch rejected: original_snippet doesn't match"

**Cause:** The code Mistral extracted doesn't match the current file (likely edited after indexing).

**Fix:** This is intentional — the agent won't guess. Re-index and the agent will pick up the new code on the next run.

### "Re-run tests still fail after the fix"

**Cause:** Mistral proposed an incomplete fix, or the root cause is elsewhere.

**Action:** The agent will retry up to 2 times, then comment on the PR with diagnostic details. Check the comment for hints or adjust the RAG context.

### Mistral API rate limits

Each fix attempt makes ~1–3 Mistral API calls. If you see rate-limit errors:
- Increase delay between retries in `agent/config.py` (`RETRY_DELAY_SECONDS`)
- Contact Mistral support to raise your quota

## Architecture overview

| Module | Purpose |
|--------|---------|
| `agent/main.py` | FastAPI app; routes `/debug/from-log`, `/index`, `/health` |
| `agent/context.py` | Parses tracebacks, extracts failing function via AST |
| `agent/indexer.py` | Embeds repo code into ChromaDB for RAG |
| `agent/chains.py` | LangChain chains for fix generation (`FixResponse`, `RefactorSuggestion`) |
| `agent/patcher.py` | Applies patches, creates branches, runs tests |
| `agent/github_client.py` | GitHub API wrapper (branches, PRs, comments) |
| `agent/config.py` | Guardrails and retry logic |
| `tests/test_context.py` | Smoke tests (no API keys) |

## Not yet built (next steps)

- **Refactoring pass:** `/refactor` wiring to static analysis (ruff/radon) — the `RefactorSuggestion` schema and `generate_refactor_suggestion` chain already exist in `agent/chains.py`; just needs a CI trigger and PR-comment poster.
- **Dashboard:** Streamlit UI for run history, pass rate, and agent decisions — logging hooks ready in `orchestrator.py`.
- **Multi-language support:** Currently Python/pytest only; Rust/Go/JavaScript support planned.
- **Custom LLM backends:** Currently Mistral only; OpenAI/Claude/Llama integration scaffolding is ready.

## Contributing

Bug reports and feature requests welcome! Please open an issue with:
- Error message or traceback
- Steps to reproduce
- Your Python and ChromaDB versions

## License

MIT (see LICENSE file)
