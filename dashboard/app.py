import logging

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent.schemas import DebugRequest, DebugResult
from agent.orchestrator import run_debug_loop
from agent.indexer import build_or_update_index
from agent.context import parse_pytest_log

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Auto-Debugging & Refactoring CI/CD Agent")


class IndexRequest(BaseModel):
    repo_path: Optional[str] = None
    changed_files: Optional[list[str]] = None


@app.get("/health")
def health():
    return {"status": "ok"}


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