"""
Applies a FixResponse to the working copy on a new branch, then re-runs
pytest to verify it actually fixes the failure before anything is opened
as a PR. Never touches main, never touches blocked paths.
"""
import logging
import subprocess
import uuid
from pathlib import Path

from git import Repo, GitCommandError

from agent.config import settings
from agent.schemas import FixResponse

logger = logging.getLogger("debug_agent")


class UnsafePatchError(Exception):
    pass


def _assert_path_allowed(file_path: str):
    normalized = file_path.replace("\\", "/").lstrip("/")
    if any(normalized.startswith(p) for p in settings.BLOCKED_PATH_PREFIXES):
        raise UnsafePatchError(f"Refusing to touch blocked path: {file_path}")
    if not normalized.endswith(settings.ALLOWED_FILE_EXTENSIONS):
        raise UnsafePatchError(f"Refusing to touch non-code file: {file_path}")


def _find_line_range(source_lines: list[str], snippet_lines: list[str]) -> tuple[int, int] | None:
    """
    Whitespace-tolerant fallback matcher: compares lines by their stripped
    content rather than exact bytes, so LLM-introduced indentation drift
    (a very common failure mode) doesn't block an otherwise-correct patch.
    Returns (start, end) as an inclusive line-index range into source_lines,
    or None if no contiguous match is found.
    """
    snippet_stripped = [l.strip() for l in snippet_lines if l.strip()]
    if not snippet_stripped:
        return None
    n = len(snippet_stripped)
    for start in range(len(source_lines) - n + 1):
        window = [source_lines[start + i].strip() for i in range(n)]
        if window == snippet_stripped:
            return start, start + n - 1
    return None


def _reindent_to_match(fixed_lines: list[str], original_first_line: str, snippet_first_line: str) -> list[str]:
    """Shifts fixed_snippet's indentation to match the real file's indentation level."""
    real_indent = len(original_first_line) - len(original_first_line.lstrip(" "))
    snippet_indent = len(snippet_first_line) - len(snippet_first_line.lstrip(" "))
    delta = real_indent - snippet_indent
    if delta == 0:
        return fixed_lines
    out = []
    for line in fixed_lines:
        if delta > 0:
            out.append(" " * delta + line)
        else:
            stripped_amount = min(-delta, len(line) - len(line.lstrip(" ")))
            out.append(line[stripped_amount:])
    return out


def apply_fix(repo_path: str, fix: FixResponse) -> None:
    """
    Replaces original_snippet with fixed_snippet in the target file.
    Tries an exact match first; if the LLM's snippet drifted on whitespace
    (common), falls back to a line-content match and re-indents the
    replacement to fit the file's actual indentation.
    """
    _assert_path_allowed(fix.file_path)
    if not fix.is_safe_to_apply:
        raise UnsafePatchError(f"LLM marked this fix unsafe to apply: {fix.explanation}")

    target = Path(repo_path) / fix.file_path
    if not target.exists():
        raise FileNotFoundError(f"{target} does not exist in repo")

    source = target.read_text(encoding="utf-8-sig")

    if fix.original_snippet in source:
        patched = source.replace(fix.original_snippet, fix.fixed_snippet, 1)
        target.write_text(patched, encoding="utf-8")
        logger.info("Patch applied via exact match: %s", fix.file_path)
        return

    logger.warning(
        "Exact match failed for %s, trying whitespace-tolerant fallback", fix.file_path
    )
    source_lines = source.splitlines()
    snippet_lines = fix.original_snippet.splitlines()
    match = _find_line_range(source_lines, snippet_lines)

    if match is None:
        # Dump both sides so the real discrepancy is visible in logs instead
        # of guessing blindly -- repr() surfaces whitespace/tab differences
        # that look identical when printed normally.
        logger.error("MISMATCH DEBUG -- LLM's original_snippet (repr, line by line):")
        for line in snippet_lines:
            logger.error("  SNIPPET: %r", line)
        logger.error("MISMATCH DEBUG -- actual lines in file containing AIMessage/HumanMessage:")
        for i, line in enumerate(source_lines):
            if "AIMessage" in line or "HumanMessage" in line or "for m in" in line:
                logger.error("  FILE[%d]: %r", i + 1, line)
        raise ValueError(
            "original_snippet not found in file (even with whitespace-tolerant "
            "matching) -- refusing to apply. The LLM likely referenced code "
            "that no longer matches the current file content."
        )

    start, end = match
    fixed_lines = fix.fixed_snippet.splitlines()
    reindented = _reindent_to_match(fixed_lines, source_lines[start], snippet_lines[0] if snippet_lines else "")

    new_lines = source_lines[:start] + reindented + source_lines[end + 1 :]
    target.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    logger.info(
        "Patch applied via whitespace-tolerant match at lines %d-%d: %s",
        start + 1, end + 1, fix.file_path,
    )


def ensure_clean_checkout(repo_path: str, base_branch: str) -> None:
    """
    Guarantees the working tree is on base_branch with zero local
    modifications before any patch is applied. Without this, leftover
    uncommitted changes from a previous (possibly crashed or interrupted)
    run silently corrupt the starting point for the next run -- the LLM's
    original_snippet may no longer exist in an already-modified file,
    causing confusing "snippet not found" failures that have nothing to
    do with the current fix attempt.
    """
    repo = Repo(repo_path)
    repo.git.checkout(base_branch)
    if repo.is_dirty(untracked_files=False):
        logger.warning(
            "Working tree had uncommitted changes on %s -- discarding them "
            "to ensure a clean baseline", base_branch,
        )
        repo.git.reset("--hard", base_branch)


def create_fix_branch(repo_path: str, run_id: str = None) -> str:
    """
    Always includes a short random suffix so re-running the agent against the
    same failing test never collides with a branch left over from an earlier
    attempt. If a collision somehow still occurs, retries once with a fresh
    suffix rather than failing the whole run.
    """
    repo = Repo(repo_path)
    prefix = run_id or "fix"
    for _ in range(3):
        branch_name = f"agent-fix/{prefix}-{uuid.uuid4().hex[:8]}"
        try:
            repo.git.checkout("-b", branch_name)
            return branch_name
        except GitCommandError as e:
            if "already exists" in str(e):
                continue
            raise
    raise RuntimeError("Could not create a unique fix branch after 3 attempts")


def commit_and_push(
    repo_path: str, branch_name: str, message: str, push_to_repo_full_name: str | None = None
) -> None:
    """
    Commits staged changes and pushes branch_name.

    By default pushes to "origin" (the repo the working copy was cloned
    from). If push_to_repo_full_name is given (a fork, e.g. from
    github_client.ensure_fork), pushes there instead via a temporary
    authenticated remote -- needed when the agent doesn't have write
    access to the original repo and must go through a fork-based PR.
    """
    repo = Repo(repo_path)
    repo.git.add(A=True)
    repo.index.commit(message)

    if push_to_repo_full_name:
        push_url = f"https://{settings.GITHUB_TOKEN}@github.com/{push_to_repo_full_name}.git"
        remote_name = "agent_fork_push"
        existing_names = [r.name for r in repo.remotes]
        if remote_name in existing_names:
            repo.delete_remote(remote_name)
        remote = repo.create_remote(remote_name, push_url)
        try:
            remote.push(refspec=f"{branch_name}:{branch_name}", force=True)
        finally:
            repo.delete_remote(remote_name)
    else:
        origin = repo.remote(name="origin")
        origin.push(refspec=f"{branch_name}:{branch_name}")


def run_tests(repo_path: str, test_name: str | None = None, test_file_path: str | None = None) -> tuple[bool, str]:
    """
    Returns (passed, output). Runs the whole suite unless a specific test
    node id can be built from test_file_path::test_name.

    Uses settings.TEST_PYTHON_EXECUTABLE rather than a bare "python" --
    the server process's own interpreter (this repo's venv) is very likely
    NOT the same interpreter that has the target repo's pytest and its
    dependencies installed, which would silently make every verification
    fail regardless of whether the actual code fix was correct.
    """
    cmd = [settings.TEST_PYTHON_EXECUTABLE, "-m", "pytest", "--tb=long", "-q"]
    if test_name:
        if test_file_path:
            node_id = f"{test_file_path.replace(chr(92), '/')}::{test_name}"
        elif "::" in test_name:
            node_id = test_name
        else:
            # No file path available -- fall back to pytest's -k substring
            # match rather than an invalid bare "::name" selector.
            cmd.extend(["-k", test_name])
            node_id = None
        if node_id:
            cmd.append(node_id)

    try:
        result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=600)
    except FileNotFoundError as e:
        return False, (
            f"Could not execute '{settings.TEST_PYTHON_EXECUTABLE}' -- check that "
            f"TEST_PYTHON_EXECUTABLE in .env points to a valid Python interpreter "
            f"that has pytest and the target repo's dependencies installed. ({e})"
        )
    passed = result.returncode == 0
    return passed, result.stdout + "\n" + result.stderr


def discard_branch(repo_path: str, branch_name: str) -> None:
    """Cleans up a failed attempt: back to main/original branch, drop the fix branch."""
    repo = Repo(repo_path)
    repo.git.checkout(repo.git.rev_parse("--abbrev-ref", "HEAD@{-1}"))
    repo.git.branch("-D", branch_name)