from typing import Optional
from pydantic import BaseModel, Field


class FailureContext(BaseModel):
    """What the CI runner sends us about a failing test."""
    test_name: str
    file_path: str
    line_number: Optional[int] = None
    exception_type: str
    exception_message: str
    traceback_text: str


class FixResponse(BaseModel):
    """Structured output the LLM must return for a proposed fix."""
    explanation: str = Field(description="Plain-English reason for the failure and the fix")
    file_path: str = Field(description="Path of the file to modify, relative to repo root")
    original_snippet: str = Field(description="Exact existing code block to replace, verbatim")
    fixed_snippet: str = Field(description="Replacement code block")
    confidence: float = Field(description="0.0-1.0 confidence this fix resolves the failure")
    is_safe_to_apply: bool = Field(
        description="False if the fix touches config/secrets/workflow files or is too risky to auto-apply"
    )


class RefactorSuggestion(BaseModel):
    file_path: str
    issue: str = Field(description="What static analysis flagged, e.g. high complexity")
    suggestion: str = Field(description="Proposed refactor, described in words")
    original_snippet: str
    suggested_snippet: str


class DebugRequest(BaseModel):
    repo: str
    branch: str
    failure: FailureContext


class DebugResult(BaseModel):
    verdict: str  # "fixed" | "failed_to_fix" | "unsafe" | "error" | "issue_opened"
    attempts: int
    pr_url: Optional[str] = None
    issue_url: Optional[str] = None
    issue_number: Optional[int] = None
    last_fix: Optional[FixResponse] = None
    message: str


class PendingFix(BaseModel):
    """
    A verified-but-not-yet-pushed fix, waiting on maintainer approval via
    the issue the agent opened. Persisted to disk so approval can be
    checked and acted on later, independent of the original request.
    """
    repo: str
    branch: str
    issue_number: int
    issue_url: str
    failure: FailureContext
    fix: FixResponse
    created_at: float