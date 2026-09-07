# Auto-Debugging & Refactoring CI/CD Agent

LangChain + Mistral + ChromaDB agent that watches CI test runs, diagnoses
failures with RAG over the repo, proposes a minimal fix, verifies it by
re-running tests on a throwaway branch, and opens a PR only if it's proven
to pass.

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

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in MISTRAL_API_KEY, GITHUB_TOKEN, GITHUB_REPO
```

Run the service locally:

```bash
uvicorn agent.main:app --reload --port 8000
```

Index a repo once before first use (subsequent runs can pass `changed_files`
for incremental updates):

```bash
curl -X POST "http://localhost:8000/index" -d '{"repo_path": "/path/to/target/repo"}' -H "Content-Type: application/json"
```

In the target repo's CI, add `AGENT_URL` as a repo secret pointing at your
deployed instance of this service, then drop in
`.github/workflows/agent-debug.yml`.

## Smoke test (no API keys needed)

```bash
python tests/test_context.py
```

Validates the traceback parser and AST function-extraction — the two
pieces that must be correct before anything reaches the LLM.

## Guardrails (agent/config.py)

- Max 2 fix attempts per failure before bailing out with a comment.
- Never touches `.github/workflows/`, `.env`, `secrets/`.
- Only edits `.py` files.
- Patch is applied via exact verbatim string match, not a fuzzy diff — if
  the LLM's `original_snippet` doesn't match the file exactly, the patch
  is rejected rather than guessed at.
- Every fix is re-verified against the real test suite on a disposable
  branch before a PR is opened; nothing is pushed to `main` directly.

## Not yet built (next steps)

- `/refactor` wiring to a static-analysis pass (ruff/radon) — schema
  (`RefactorSuggestion`) and chain (`generate_refactor_suggestion`) already
  exist in `agent/chains.py`, just needs a CI trigger + PR-comment poster.
- Dashboard (Streamlit) for run history / pass rate — logging hooks would
  go in `orchestrator.py`.
- Multi-language support (currently Python/pytest only).
