import json
import logging
import time
from pathlib import Path

from git import Repo

from agent.config import settings
from agent.context import extract_enclosing_function, resolve_application_target
from agent.chains import generate_fix
from agent.workspace import ensure_repo_cloned, ensure_deps_installed
from agent.patcher import (
    apply_fix,
    create_fix_branch,
    commit_and_push,
    run_tests,
    ensure_clean_checkout,
    UnsafePatchError,
)
from agent.github_client import open_fix_pr, has_push_access, ensure_fork, open_issue, comment_on_issue
from agent.schemas import DebugRequest, DebugResult, FixResponse, PendingFix
from agent import pending_fixes

logger = logging.getLogger("debug_agent")

RUN_LOG_PATH = Path(settings.CHROMA_PERSIST_DIR).parent / "runs.jsonl"


def _log_run(req: DebugRequest, result: DebugResult, started_at: float) -> None:
    """Appends one JSON line per run for the dashboard to read."""
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": started_at,
        "repo": req.repo,
        "branch": req.branch,
        "test_name": req.failure.test_name,
        "exception_type": req.failure.exception_type,
        "verdict": result.verdict,
        "attempts": result.attempts,
        "duration_sec": round(time.time() - started_at, 2),
        "pr_url": result.pr_url,
        "confidence": result.last_fix.confidence if result.last_fix else None,
        "explanation": result.last_fix.explanation if result.last_fix else result.message,
    }
    with open(RUN_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def run_debug_loop(req: DebugRequest) -> DebugResult:
    started_at = time.time()
    result = _run_debug_loop_inner(req)
    _log_run(req, result, started_at)
    return result


def _build_issue_body(failure, fix: FixResponse) -> str:
    return (
        f"**Bug detected in `{fix.file_path}`**"
        + (f" (near line {failure.line_number})" if failure.line_number else "")
        + "\n\n"
        f"**Failing test:** `{failure.test_name}`\n"
        f"**Error:** `{failure.exception_type}: {failure.exception_message}`\n\n"
        f"**Diagnosis:**\n{fix.explanation}\n\n"
        f"**Proposed fix** (confidence: {fix.confidence:.2f}), verified locally against "
        "the failing test:\n\n"
        "```diff\n"
        + "\n".join(f"- {line}" for line in fix.original_snippet.splitlines())
        + "\n"
        + "\n".join(f"+ {line}" for line in fix.fixed_snippet.splitlines())
        + "\n```\n\n"
        "---\n"
        "This proposal was generated and verified automatically by a debugging agent "
        "-- it is **not** applied yet. If you'd like this turned into a pull request, "
        "reply with `/approve-fix` or react with 👍 on this issue. No changes will be "
        "made otherwise."
    )


def finalize_approved_fix(pf: PendingFix) -> dict:
    """
    Called once a maintainer has approved a pending fix's issue. Re-clones
    the repo fresh (it may have changed since the proposal), re-applies
    the already-verified fix, re-confirms tests still pass, then actually
    pushes (via fork) and opens the PR -- only now touching the repo.
    """

    repo_path = ensure_repo_cloned(pf.repo, pf.branch)
    ensure_deps_installed(repo_path)
    ensure_clean_checkout(repo_path, pf.branch)

    branch_name = create_fix_branch(repo_path, run_id=f"{pf.failure.test_name[:20]}")

    try:
        apply_fix(repo_path, pf.fix)
    except (UnsafePatchError, FileNotFoundError, ValueError) as e:
        logger.warning("Approved fix no longer applies cleanly for %s#%d: %s", pf.repo, pf.issue_number, e)
        comment_on_issue(
            pf.repo, pf.issue_number,
            "This approved fix no longer applies cleanly -- the code has likely "
            "changed since the proposal. Re-run the agent on this repo to get a "
            "fresh diagnosis.",
        )
        pending_fixes.remove_pending_fix(pf.repo, pf.issue_number)
        return {"repo": pf.repo, "issue_number": pf.issue_number, "status": "stale", "pr_url": None}

    passed, output = run_tests(
        repo_path, test_name=pf.failure.test_name, test_file_path=pf.failure.file_path
    )
    if not passed:
        logger.warning("Approved fix no longer passes tests for %s#%d", pf.repo, pf.issue_number)
        comment_on_issue(
            pf.repo, pf.issue_number,
            "This approved fix no longer makes the test pass -- the code has likely "
            "changed since the proposal. Re-run the agent on this repo to get a "
            "fresh diagnosis.",
        )
        pending_fixes.remove_pending_fix(pf.repo, pf.issue_number)
        return {"repo": pf.repo, "issue_number": pf.issue_number, "status": "stale", "pr_url": None}

    fork_full_name = ensure_fork(pf.repo)
    commit_and_push(
        repo_path, branch_name, f"agent-fix: resolve {pf.failure.test_name}",
        push_to_repo_full_name=fork_full_name,
    )
    pr_url = open_fix_pr(
        pf.repo, branch_name, pf.branch, pf.fix, pf.failure.test_name,
        head_repo_full_name=fork_full_name,
    )
    comment_on_issue(pf.repo, pf.issue_number, f"✅ Approved and verified -- PR opened: {pr_url}")
    pending_fixes.remove_pending_fix(pf.repo, pf.issue_number)

    return {"repo": pf.repo, "issue_number": pf.issue_number, "status": "pr_opened", "pr_url": pr_url}


def _run_debug_loop_inner(req: DebugRequest) -> DebugResult:
    failure = req.failure
    # LOCAL DEV: if REPO_PATH is set, use that fixed path (your laptop
    # workflow so far). DEPLOYED: REPO_PATH is unset, so the agent clones
    # (or updates) the target repo itself and installs its dependencies
    # so its own test suite can actually run in this container.
    if settings.REPO_PATH:
        repo_path = settings.REPO_PATH
    else:
        repo_path = ensure_repo_cloned(req.repo, req.branch)
        ensure_deps_installed(repo_path)

    ensure_clean_checkout(repo_path, req.branch)

    # Decide up front whether we can push directly to req.repo, or need to
    # go through the issue-first flow instead -- for repos you don't own
    # or aren't a collaborator on. This is just a read-only permission
    # check; the fork itself (if ever needed) only happens later, at
    # approval time in finalize_approved_fix -- not here. Forking eagerly
    # would fail the whole request before the issue even gets opened if
    # the token can't fork (a known limitation of fine-grained PATs on
    # repos you're not a member of), which is strictly worse: the person
    # should still see a proposal even if pushing it later needs a
    # different token.
    needs_issue_first = False
    if not settings.REPO_PATH:
        if not has_push_access(req.repo):
            logger.info(
                "No push access to %s -- will use issue-first flow instead of a direct PR",
                req.repo,
            )
            needs_issue_first = True

    # If the traceback points at the test file itself (single-frame
    # assertion, no deeper call stack), the real bug is almost always in
    # application code the test imports -- follow those imports to find
    # and show the LLM the actual implementation, not the test's own body.
    target_file_path = failure.file_path
    if "test" in Path(failure.file_path).name.lower():
        resolved = resolve_application_target(repo_path, failure.file_path)
        if resolved:
            target_file_path, function_source = resolved
            logger.info(
                "Resolved test failure to application code: %s (via imports in %s)",
                target_file_path, failure.file_path,
            )
        else:
            function_source = extract_enclosing_function(
                repo_path, failure.file_path, failure.line_number or 1
            )
    else:
        function_source = extract_enclosing_function(
            repo_path, failure.file_path, failure.line_number or 1
        )

    branch_name = None
    last_fix: FixResponse | None = None
    retry_context = ""

    try:
        for attempt in range(1, settings.MAX_FIX_ATTEMPTS + 1):
            logger.info("Attempt %d/%d for %s", attempt, settings.MAX_FIX_ATTEMPTS, failure.test_name)

            last_fix = generate_fix(
                test_name=failure.test_name,
                exception_type=failure.exception_type,
                exception_message=failure.exception_message,
                traceback_text=failure.traceback_text,
                file_path=target_file_path,
                function_source=function_source,
                retry_context=retry_context,
            )

            if branch_name is None:
                branch_name = create_fix_branch(repo_path, run_id=f"{failure.test_name[:20]}")

            try:
                apply_fix(repo_path, last_fix)
            except UnsafePatchError as e:
                logger.warning("Unsafe patch on attempt %d: %s", attempt, e)
                return DebugResult(
                    verdict="unsafe",
                    attempts=attempt,
                    last_fix=last_fix,
                    message=str(e),
                )
            except (FileNotFoundError, ValueError) as e:
                logger.warning("Patch application failed on attempt %d: %s", attempt, e)
                retry_context = str(e)
                continue

            passed, output = run_tests(
                repo_path, test_name=failure.test_name, test_file_path=failure.file_path
            )
            if passed:
                if needs_issue_first:
                    # EXTERNAL REPO: issue-first workflow. The fix is verified
                    # (tests pass locally) but nothing is pushed or opened as
                    # a PR yet -- propose it via an issue and wait for a
                    # maintainer to explicitly approve before touching their
                    # repo further. This mirrors standard OSS contribution
                    # etiquette rather than surprising maintainers with
                    # unsolicited PRs.
                    issue_body = _build_issue_body(failure, last_fix)
                    issue_number, issue_url = open_issue(
                        req.repo,
                        title=f"Bug detected: {failure.test_name} fails ({failure.exception_type})",
                        body=issue_body,
                    )
                    pending_fixes.save_pending_fix(
                        PendingFix(
                            repo=req.repo,
                            branch=req.branch,
                            issue_number=issue_number,
                            issue_url=issue_url,
                            failure=failure,
                            fix=last_fix,
                            created_at=time.time(),
                        )
                    )
                    return DebugResult(
                        verdict="issue_opened",
                        attempts=attempt,
                        issue_url=issue_url,
                        issue_number=issue_number,
                        last_fix=last_fix,
                        message=(
                            f"Fix verified locally. Opened issue #{issue_number} "
                            "proposing it -- waiting for maintainer approval before "
                            "opening a PR."
                        ),
                    )
                else:
                    # OWN REPO / collaborator access: push and open the PR directly.
                    commit_and_push(
                        repo_path,
                        branch_name,
                        f"agent-fix: resolve {failure.test_name}",
                    )
                    pr_url = open_fix_pr(
                        req.repo,
                        branch_name,
                        req.branch,
                        last_fix,
                        failure.test_name,
                    )
                    return DebugResult(
                        verdict="fixed",
                        attempts=attempt,
                        pr_url=pr_url,
                        last_fix=last_fix,
                        message="Fix verified and PR opened.",
                    )

            # still failing -- feed the new output back in for the next attempt
            retry_context = output[-3000:]  # keep prompt bounded

        return DebugResult(
            verdict="failed_to_fix",
            attempts=settings.MAX_FIX_ATTEMPTS,
            last_fix=last_fix,
            message=(
                "Could not produce a passing fix within the attempt limit. "
                f"Last failure reason: {retry_context[:500] if retry_context else 'unknown'}"
            ),
        )
    finally:
        # No matter how this run ends -- success, failure, or an exception --
        # the shared working directory must be left on the base branch.
        # REPO_PATH is very likely the SAME physical folder the person is
        # working in themselves; leaving it checked out on a throwaway
        # agent-fix/* branch silently corrupts their next manual command
        # (stale-looking file content, wrong git status, commits landing on
        # the wrong branch) with no visible error at the time it happens.
        try:
            repo = Repo(repo_path)
            if repo.active_branch.name != req.branch:
                logger.info(
                    "Returning working directory from '%s' to base branch '%s'",
                    repo.active_branch.name, req.branch,
                )
                repo.git.checkout(req.branch)
        except Exception as e:
            logger.error(
                "Could not restore working directory to base branch '%s': %s "
                "-- the repo may be left on a fix branch, check manually.",
                req.branch, e,
            )