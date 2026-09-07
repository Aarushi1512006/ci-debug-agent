"""
Persists fixes that have been verified but not yet pushed, while they
wait for a maintainer to approve the issue the agent opened proposing
them. This is deliberately simple (one JSON file) -- the agent is a
single instance, and pending fixes are a small, low-volume list.
"""
import json
import logging
from pathlib import Path

from agent.config import settings
from agent.schemas import PendingFix

logger = logging.getLogger("debug_agent")


def _store_path() -> Path:
    return Path(settings.CHROMA_PERSIST_DIR).parent / "pending_fixes.json"


def load_all() -> list[PendingFix]:
    path = _store_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [PendingFix(**item) for item in raw]
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Could not read pending fixes store (%s), treating as empty", e)
        return []


def _save_all(items: list[PendingFix]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([item.model_dump() for item in items], indent=2),
        encoding="utf-8",
    )


def save_pending_fix(pf: PendingFix) -> None:
    items = load_all()
    items.append(pf)
    _save_all(items)
    logger.info("Saved pending fix for %s issue #%d", pf.repo, pf.issue_number)


def remove_pending_fix(repo: str, issue_number: int) -> None:
    items = load_all()
    remaining = [
        i for i in items if not (i.repo == repo and i.issue_number == issue_number)
    ]
    _save_all(remaining)
    logger.info("Removed pending fix for %s issue #%d", repo, issue_number)