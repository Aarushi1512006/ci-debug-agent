"""
Parses pytest's captured output into a FailureContext, and pulls the full
surrounding function body from source (via ast) rather than just the
offending line -- the LLM needs the whole function to propose a real fix.
"""
import ast
import logging
import re
from pathlib import Path
from typing import Optional

from agent.schemas import FailureContext

logger = logging.getLogger("debug_agent")

# Matches pytest's "FAILED path/to/test.py::test_name" summary line
_FAILED_LINE_RE = re.compile(r"^FAILED\s+(?P<file>\S+)::(?P<test>\S+)", re.MULTILINE)
# Matches "File "path", line 123, in some_func" frames inside a full traceback
_FRAME_RE = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)')
# Fallback: pytest's short-form location line, e.g. "tests/test_x.py:18: AssertionError"
# This is what appears when the failure is a single-frame assertion inside the test
# itself, with no deeper call stack -- the File "..." pattern above won't match it.
_SHORT_LOC_RE = re.compile(r"^(?P<file>\S+\.py):(?P<line>\d+):\s*\S", re.MULTILINE)
# Matches the final "ExceptionType: message" line of a traceback
_EXC_RE = re.compile(r"^(?P<type>[\w\.]+Error|[\w\.]+Exception):\s*(?P<msg>.*)$")


def parse_pytest_log(log_text: str) -> Optional[FailureContext]:
    """Pulls the first failing test's details out of a pytest --tb=long log."""
    failed_match = _FAILED_LINE_RE.search(log_text)
    if not failed_match:
        return None

    test_file = failed_match.group("file")
    test_name = failed_match.group("test")

    # Find the traceback block for this test (best-effort: take the first
    # traceback section preceding the FAILED summary line)
    tb_start = log_text.find("Traceback (most recent call last):")
    traceback_text = log_text[tb_start:] if tb_start != -1 else log_text

    exc_type, exc_msg = "UnknownError", ""
    for line in reversed(traceback_text.splitlines()):
        stripped = line.strip()
        # pytest prefixes assertion/exception summary lines with "E   "
        if stripped.startswith("E "):
            stripped = stripped[1:].strip()
        m = _EXC_RE.match(stripped)
        if m:
            exc_type, exc_msg = m.group("type"), m.group("msg")
            break

    frames = _FRAME_RE.findall(traceback_text)
    if frames:
        file_path, line_number = frames[-1][0], int(frames[-1][1])
    else:
        # No "File "..."" frames -- likely a single-frame assertion failure
        # inside the test itself. Try pytest's short location line instead.
        short_match = _SHORT_LOC_RE.search(traceback_text) or _SHORT_LOC_RE.search(log_text)
        if short_match:
            file_path, line_number = short_match.group("file"), int(short_match.group("line"))
        else:
            file_path, line_number = test_file, None

    return FailureContext(
        test_name=test_name,
        file_path=file_path,
        line_number=line_number,
        exception_type=exc_type,
        exception_message=exc_msg,
        traceback_text=traceback_text.strip(),
    )


def _find_function_by_name(repo_root: str, module_filename: str, func_name: str) -> tuple[str, str] | None:
    """
    Searches the repo for a file matching module_filename and returns
    (relative_path, function_source) for the named function inside it,
    found by AST name lookup rather than a line number.
    """
    for candidate in Path(repo_root).rglob(module_filename):
        if any(part in ("__pycache__", ".git", "venv", ".venv") for part in candidate.parts):
            continue
        try:
            source = candidate.read_text(encoding="utf-8-sig", errors="ignore")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                lines = source.splitlines()
                func_source = "\n".join(lines[node.lineno - 1 : node.end_lineno])
                rel_path = str(candidate.relative_to(repo_root)).replace("\\", "/")
                return rel_path, func_source
    return None


def resolve_application_target(repo_root: str, test_file_path: str) -> tuple[str, str] | None:
    """
    When a test failure's traceback points at the test file itself (a
    single-frame assertion, no deeper call stack -- see _SHORT_LOC_RE),
    the actual bug almost always lives in application code the test
    imports and calls, not in the test file. This follows the test's
    own `from module import name` statements, finds which imported name
    is actually called in the test body, and locates that function's
    real source via AST -- so the LLM gets shown the real buggy
    implementation instead of guessing its shape from RAG context alone.

    Returns (relative_file_path, function_source) for the resolved
    target, or None if no such function could be confidently resolved.
    """
    full_test_path = Path(repo_root) / test_file_path
    logger.info("resolve_application_target: repo_root=%r test_file_path=%r -> full_test_path=%r", repo_root, test_file_path, str(full_test_path))
    if not full_test_path.exists():
        logger.warning("resolve_application_target: full_test_path does not exist, giving up: %s", full_test_path)
        return None

    try:
        test_source = full_test_path.read_text(encoding="utf-8-sig", errors="ignore")
        tree = ast.parse(test_source)
    except (SyntaxError, OSError) as e:
        logger.warning("resolve_application_target: could not parse test file: %s", e)
        return None

    # Map imported_name -> module filename (e.g. "build_messages" -> "UIchatbot.py")
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            module_filename = node.module.split(".")[-1] + ".py"
            for alias in node.names:
                imported[alias.asname or alias.name] = module_filename

    logger.info("resolve_application_target: imported names found = %r", imported)

    if not imported:
        logger.warning("resolve_application_target: no ImportFrom statements found in test file")
        return None

    # Find which imported name is actually called as a function in the test body
    called_names = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    logger.info("resolve_application_target: called_names in test body = %r", called_names)

    for name in called_names:
        if name in imported:
            logger.info("resolve_application_target: trying to locate '%s' in %s", name, imported[name])
            result = _find_function_by_name(repo_root, imported[name], name)
            if result:
                logger.info("resolve_application_target: SUCCESS, found %s", result[0])
                return result
            else:
                logger.warning("resolve_application_target: _find_function_by_name found no match for '%s' in '%s' under repo_root=%s", name, imported[name], repo_root)

    logger.warning("resolve_application_target: no called name matched an import, giving up")
    return None


def extract_enclosing_function(repo_root: str, file_path: str, line_number: int) -> str:
    """
    Returns the full source of the function/method that contains `line_number`.
    Falls back to a +/-15 line window if AST parsing fails (e.g. syntax error file).
    """
    full_path = Path(repo_root) / file_path
    if not full_path.exists():
        return ""

    source = full_path.read_text(encoding="utf-8-sig", errors="ignore")

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _fallback_window(source, line_number)

    best_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            if start <= line_number <= end:
                # prefer the innermost (smallest) enclosing function
                if best_node is None or (end - start) < (best_node.end_lineno - best_node.lineno):
                    best_node = node

    if best_node is None:
        return _fallback_window(source, line_number)

    lines = source.splitlines()
    return "\n".join(lines[best_node.lineno - 1 : best_node.end_lineno])


def _fallback_window(source: str, line_number: int, window: int = 15) -> str:
    lines = source.splitlines()
    lo = max(0, (line_number or 1) - window)
    hi = min(len(lines), (line_number or 1) + window)
    return "\n".join(lines[lo:hi])