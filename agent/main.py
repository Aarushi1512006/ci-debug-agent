import logging

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent.schemas import DebugRequest, DebugResult
from agent.orchestrator import run_debug_loop, finalize_approved_fix
from agent.indexer import build_or_update_index
from agent.context import parse_pytest_log
from agent.workspace import parse_repo_full_name, ensure_repo_cloned, ensure_deps_installed
from agent.patcher import run_tests
from agent.github_client import issue_has_approval
from agent import pending_fixes
from agent.ui import INDEX_HTML

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_agent")

app = FastAPI(title="Auto-Debugging & Refactoring CI/CD Agent")


class IndexRequest(BaseModel):
    repo_path: Optional[str] = None
    changed_files: Optional[list[str]] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def ui_home():
    return INDEX_HTML


@app.post("/index")
def index_repo(req: IndexRequest):
    """Called on push to (re)index the repo before/after a run."""
    build_or_update_index(repo_path=req.repo_path, changed_files=req.changed_files)
    return {"status": "indexed", "changed_files": req.changed_files or "all"}


@app.post("/debug", response_model=DebugResult)
def debug(req: DebugRequest):
    """
    Main entrypoint called by the GitHub Action when pytest fails.
    Runs the full context -> fix -> verify -> PR/retry/bail loop.
    """
    try:
        return run_debug_loop(req)
    except Exception as e:
        logging.exception("Unhandled error in debug loop")
        raise HTTPException(status_code=500, detail=str(e))


class DebugFromLogRequest(BaseModel):
    repo: str
    branch: str
    pytest_log: str


@app.post("/debug/from-log")
def debug_from_log(req: DebugFromLogRequest):
    """Convenience endpoint: CI posts the raw pytest log instead of pre-parsed fields."""
    failure = parse_pytest_log(req.pytest_log)
    if failure is None:
        raise HTTPException(status_code=400, detail="No failing test found in log")
    debug_req = DebugRequest(repo=req.repo, branch=req.branch, failure=failure)
    return run_debug_loop(debug_req)


class AutoDebugRequest(BaseModel):
    repo_url: str
    branch: str = "main"


@app.post("/debug/auto")
def debug_auto(req: AutoDebugRequest):
    """
    The "point at any repo" entrypoint for the UI: given just a GitHub
    repo URL, clones it, installs its own dependencies, runs its full
    test suite to find the first real failure, then runs the same
    fix -> verify -> PR loop as every other entrypoint.
    """
    try:
        repo_full_name = parse_repo_full_name(req.repo_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        repo_path = ensure_repo_cloned(repo_full_name, req.branch)
    except Exception as e:
        logger.exception("Failed to clone repo")
        raise HTTPException(status_code=400, detail=f"Could not clone repo: {e}")

    ensure_deps_installed(repo_path)

    logger.info("Running full test suite for %s to find a failure", repo_full_name)
    passed, output = run_tests(repo_path)
    if passed:
        return {
            "status": "no_failures",
            "message": "All tests passed -- nothing for the agent to fix.",
        }

    failure = parse_pytest_log(output)
    if failure is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Tests failed but the agent could not identify a specific "
                "failing test from the output. Raw output (truncated): "
                + output[-1500:]
            ),
        )

    debug_req = DebugRequest(repo=repo_full_name, branch=req.branch, failure=failure)
    try:
        return run_debug_loop(debug_req)
    except Exception as e:
        logger.exception("Unhandled error in auto debug loop")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/pending")
def list_pending():
    """
    Lists fixes that have been verified but are waiting on maintainer
    approval (via /approve-fix or a 👍 on the issue the agent opened).
    """
    return {"pending": [pf.model_dump() for pf in pending_fixes.load_all()]}


class CheckApprovalsRequest(BaseModel):
    repo: Optional[str] = None  # if omitted, checks every pending fix


@app.post("/approvals/check")
def check_approvals(req: CheckApprovalsRequest):
    """
    For each pending fix (optionally filtered to one repo), checks
    whether its issue has been approved. Approved ones get pushed
    (via fork) and turned into a real PR; the issue gets a comment
    with the result either way. Call this periodically (e.g. a
    scheduled GitHub Action, or a cron hitting this endpoint) since
    approval can happen at any time, independent of the original run.
    """
    all_pending = pending_fixes.load_all()
    to_check = [pf for pf in all_pending if req.repo is None or pf.repo == req.repo]

    results = []
    for pf in to_check:
        try:
            if issue_has_approval(pf.repo, pf.issue_number):
                logger.info("Approval found for %s issue #%d, finalizing", pf.repo, pf.issue_number)
                results.append(finalize_approved_fix(pf))
            else:
                results.append({
                    "repo": pf.repo, "issue_number": pf.issue_number,
                    "status": "still_waiting", "pr_url": None,
                })
        except Exception as e:
            logger.exception("Error checking/finalizing approval for %s#%d", pf.repo, pf.issue_number)
            results.append({
                "repo": pf.repo, "issue_number": pf.issue_number,
                "status": "error", "pr_url": None, "error": str(e),
            })

    return {"checked": len(to_check), "results": results}