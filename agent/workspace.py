"""
Manages a local workspace of cloned repos for the deployed agent.

Locally (your laptop), REPO_PATH pointed at a repo you'd already cloned
by hand. On a real deployment (Render/Railway/etc), there is no such
fixed path -- the service needs to clone (or update) whichever repo a
CI request names, on demand, into its own workspace directory.
"""
import logging
from pathlib import Path

from git import Repo, GitCommandError

from agent.config import settings

logger = logging.getLogger("debug_agent")


def _authenticated_clone_url(repo_full_name: str) -> str:
    """Builds an HTTPS clone URL with the GitHub token embedded for auth."""
    token = settings.GITHUB_TOKEN
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set -- cannot clone or push to private repos")
    return f"https://{token}@github.com/{repo_full_name}.git"


def parse_repo_full_name(repo_url_or_name: str) -> str:
    """
    Normalizes any of these into "owner/repo":
      - https://github.com/owner/repo
      - https://github.com/owner/repo.git
      - https://github.com/owner/repo/
      - git@github.com:owner/repo.git
      - owner/repo (already normalized)
    """
    s = repo_url_or_name.strip()
    if s.startswith("git@github.com:"):
        s = s[len("git@github.com:"):]
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.removesuffix(".git").removesuffix("/")
    if s.count("/") != 1:
        raise ValueError(
            f"Could not parse a valid 'owner/repo' from {repo_url_or_name!r}"
        )
    return s


def ensure_repo_cloned(repo_full_name: str, branch: str = "main") -> str:
    """
    Ensures repo_full_name (e.g. "Aarushi1512006/mood-ai") is cloned
    locally under WORKSPACE_ROOT, up to date with origin/<branch>.
    Returns the local filesystem path to the clone.

    Safe to call on every request: clones once, then just fetches +
    resets on subsequent calls rather than re-cloning from scratch.
    """
    workspace_root = Path(settings.WORKSPACE_ROOT)
    workspace_root.mkdir(parents=True, exist_ok=True)

    # e.g. "Aarushi1512006/mood-ai" -> "Aarushi1512006__mood-ai" (safe dir name)
    local_dir_name = repo_full_name.replace("/", "__")
    local_path = workspace_root / local_dir_name

    if local_path.exists() and (local_path / ".git").exists():
        logger.info("Repo already cloned at %s, fetching latest", local_path)
        repo = Repo(str(local_path))
        try:
            repo.git.fetch("origin", branch)
            repo.git.checkout(branch)
            repo.git.reset("--hard", f"origin/{branch}")
            repo.git.clean("-fdx")
        except GitCommandError as e:
            logger.warning(
                "Fetch/reset failed for existing clone (%s), re-cloning from scratch", e
            )
            import shutil
            shutil.rmtree(local_path, ignore_errors=True)
            return ensure_repo_cloned(repo_full_name, branch)
    else:
        logger.info("Cloning %s (branch=%s) into %s", repo_full_name, branch, local_path)
        clone_url = _authenticated_clone_url(repo_full_name)
        Repo.clone_from(clone_url, str(local_path), branch=branch)

    return str(local_path)


def ensure_deps_installed(local_path: str) -> None:
    """
    Best-effort install of the target repo's own dependencies, so its
    test suite can actually run inside this container. Skipped if
    requirements.txt is missing, and cached via a marker file so this
    doesn't reinstall on every single request.
    """
    import subprocess

    req_file = Path(local_path) / "requirements.txt"
    if not req_file.exists():
        logger.info("No requirements.txt in %s, skipping dependency install", local_path)
        return

    marker = Path(local_path) / ".agent_deps_installed"
    req_hash = str(hash(req_file.read_text(encoding="utf-8", errors="ignore")))
    if marker.exists() and marker.read_text().strip() == req_hash:
        logger.info("Target repo dependencies already installed (unchanged), skipping")
        return

    logger.info("Installing target repo's requirements.txt (this may take a while)...")
    result = subprocess.run(
        ["pip", "install", "--break-system-packages", "-q", "-r", str(req_file)],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        logger.warning(
            "Installing target repo dependencies had errors (continuing anyway): %s",
            result.stderr[-1000:],
        )
    else:
        marker.write_text(req_hash)
        logger.info("Target repo dependencies installed successfully")