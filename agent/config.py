"""
Central configuration for the CI/CD debugging agent.
All env vars are read here so nothing else in the codebase touches os.environ directly.
"""
import os
from dotenv import load_dotenv

# override=True: .env should always win over a stray OS-level environment
# variable of the same name (e.g. left over from an earlier `setx` or a
# parent process). Without this, load_dotenv() silently keeps whatever
# was already set in the OS and ignores .env entirely -- a confusing,
# hard-to-notice bug since the file looks correct but isn't actually used.
load_dotenv(override=True)


class Settings:
    # --- LLM ---
    MISTRAL_API_KEY: str = os.environ.get("MISTRAL_API_KEY", "")
    MISTRAL_MODEL: str = os.environ.get("MISTRAL_MODEL", "mistral-small-2506")
    LLM_TEMPERATURE: float = float(os.environ.get("LLM_TEMPERATURE", "0.1"))

    # --- Embeddings / Vector store ---
    EMBEDDING_MODEL: str = os.environ.get(
        "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    CHROMA_PERSIST_DIR: str = os.environ.get("CHROMA_PERSIST_DIR", "./.chroma_store")

    # --- GitHub ---
    GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")
    GITHUB_REPO: str = os.environ.get("GITHUB_REPO", "")  # e.g. "Aarushi1512006/some-repo"

    # --- Repo working copy ---
    # LOCAL DEV ONLY: a fixed path to a repo you've already cloned by hand.
    # In a real deployment, leave this unset -- the agent instead clones
    # whichever repo a request names into WORKSPACE_ROOT on demand (see
    # agent/workspace.py). REPO_PATH is only used as a fallback when set.
    REPO_PATH: str = os.environ.get("REPO_PATH", "")

    # Where the deployed agent clones target repos into. Ephemeral disk is
    # fine here -- repos are re-cloned/reset on every run if missing.
    WORKSPACE_ROOT: str = os.environ.get("WORKSPACE_ROOT", "/tmp/agent-workspace")

    # --- Test execution ---
    # The Python interpreter used to run pytest INSIDE the target repo. This
    # must NOT default to whatever "python" resolves to for this server's
    # own process -- this server runs in its own venv (with fastapi/langchain
    # installed), which is almost certainly a different venv than the target
    # repo's (with pytest/its own dependencies installed). Point this at the
    # target repo's venv python explicitly, e.g.:
    #   TEST_PYTHON_EXECUTABLE=E:/Mood_ai/mood-ai/.venv/Scripts/python.exe
    TEST_PYTHON_EXECUTABLE: str = os.environ.get("TEST_PYTHON_EXECUTABLE", "python")

    # --- Guardrails ---
    MAX_FIX_ATTEMPTS: int = int(os.environ.get("MAX_FIX_ATTEMPTS", "2"))
    BLOCKED_PATH_PREFIXES: tuple = (
        ".github/workflows/",
        ".env",
        "secrets/",
        ".git/",
    )
    ALLOWED_FILE_EXTENSIONS: tuple = (".py",)

    # --- Approval polling ---
    # The server checks all pending (issue-first) fixes for maintainer
    # approval on a timer, so nothing has to click "Check All Now" by hand.
    AUTO_POLL_APPROVALS: bool = os.environ.get("AUTO_POLL_APPROVALS", "true").lower() == "true"
    APPROVAL_POLL_INTERVAL_SECONDS: int = int(
        os.environ.get("APPROVAL_POLL_INTERVAL_SECONDS", "120")
    )


settings = Settings()
