import logging
import time

from github import Github, GithubException

from agent.config import settings
from agent.schemas import FixResponse

logger = logging.getLogger("debug_agent")


def _gh() -> Github:
    return Github(settings.GITHUB_TOKEN)


def get_authenticated_login() -> str:
    """The GitHub username the configured token belongs to."""
    return _gh().get_user().login


def has_push_access(repo_full_name: str) -> bool:
    """
    Whether the authenticated token can push directly to repo_full_name
    (own repos, or repos where you're a collaborator). False for repos
    you can only read -- meaning a fork-based PR is needed instead.
    """
    try:
        repo = _gh().get_repo(repo_full_name)
        perms = repo.permissions
        return bool(perms and perms.push)
    except GithubException as e:
        logger.warning("Could not check push access for %s: %s", repo_full_name, e)
        return False


def ensure_fork(repo_full_name: str) -> str:
    """
    Creates a fork of repo_full_name under the authenticated user's
    account if one doesn't already exist, and returns the fork's
    "owner/repo" full name. Forking is asynchronous on GitHub's side,
    so this polls briefly until the fork is actually queryable.
    """
    gh = _gh()
    origin_repo = gh.get_repo(repo_full_name)
    user = gh.get_user()
    fork_full_name = f"{user.login}/{origin_repo.name}"

    try:
        gh.get_repo(fork_full_name)
        logger.info("Fork already exists: %s", fork_full_name)
        return fork_full_name
    except GithubException:
        pass  # doesn't exist yet, fall through to create it

    logger.info("Forking %s into %s", repo_full_name, fork_full_name)
    user.create_fork(origin_repo)

    for attempt in range(20):
        time.sleep(1.5)
        try:
            gh.get_repo(fork_full_name)
            logger.info("Fork ready after %.1fs", (attempt + 1) * 1.5)
            return fork_full_name
        except GithubException:
            continue

    raise RuntimeError(
        f"Forked {repo_full_name} but the fork wasn't ready after 30s -- try again shortly"
    )


def open_fix_pr(
    repo_full_name: str,
    branch_name: str,
    base_branch: str,
    fix: FixResponse,
    test_name: str,
    head_repo_full_name: str | None = None,
) -> str:
    """
    Opens a PR against repo_full_name. If head_repo_full_name is given
    and differs from repo_full_name, this is a cross-repo PR from a fork
    (head_repo_full_name) into the original repo -- the standard
    open-source contribution flow for repos you don't have write access to.
    """
    repo = _gh().get_repo(repo_full_name)

    head = branch_name
    is_fork_pr = head_repo_full_name and head_repo_full_name != repo_full_name
    if is_fork_pr:
        fork_owner = head_repo_full_name.split("/")[0]
        head = f"{fork_owner}:{branch_name}"

    body = (
        f"**Auto-generated fix for failing test:** `{test_name}`\n\n"
        f"**Explanation:**\n{fix.explanation}\n\n"
        f"**Confidence:** {fix.confidence:.2f}\n\n"
        f"**File changed:** `{fix.file_path}`\n\n"
        "> This PR was opened automatically by the debug agent after verifying "
        "the patch makes the previously-failing test pass. Please review before merging."
    )
    if is_fork_pr:
        body += (
            f"\n\n> Opened from a fork ({head_repo_full_name}) since the agent "
            "doesn't have write access to this repository directly."
        )

    pr = repo.create_pull(
        title=f"agent-fix: {test_name}",
        body=body,
        head=head,
        base=base_branch,
    )
    return pr.html_url


def open_issue(repo_full_name: str, title: str, body: str) -> tuple[int, str]:
    """Opens a GitHub issue and returns (issue_number, issue_url)."""
    repo = _gh().get_repo(repo_full_name)
    issue = repo.create_issue(title=title, body=body)
    return issue.number, issue.html_url


def comment_on_issue(repo_full_name: str, issue_number: int, body: str) -> None:
    repo = _gh().get_repo(repo_full_name)
    issue = repo.get_issue(number=issue_number)
    issue.create_comment(body)


APPROVAL_TRIGGERS = ("/approve-fix",)


def issue_has_approval(repo_full_name: str, issue_number: int) -> bool:
    """
    True if a maintainer has approved the proposed fix on this issue,
    via either a comment containing one of APPROVAL_TRIGGERS, or a
    thumbs-up reaction on the issue itself. False if the issue was
    closed without approval (maintainer declined).
    """
    repo = _gh().get_repo(repo_full_name)
    issue = repo.get_issue(number=issue_number)

    if issue.state == "closed":
        return False

    for comment in issue.get_comments():
        text = (comment.body or "").strip().lower()
        if any(trigger in text for trigger in APPROVAL_TRIGGERS):
            return True

    try:
        for reaction in issue.get_reactions():
            if reaction.content == "+1":
                return True
    except GithubException:
        pass  # reactions API can be flaky/unavailable in some contexts; not fatal

    return False


def post_failure_comment(repo_full_name: str, pr_number: int, explanation: str, attempts: int) -> None:
    repo = _gh().get_repo(repo_full_name)
    issue = repo.get_issue(number=pr_number)
    issue.create_comment(
        f"🤖 Auto-fix agent could not resolve this failure after {attempts} attempt(s).\n\n"
        f"Last analysis:\n{explanation}\n\n"
        "No changes were applied. Manual investigation needed."
    )