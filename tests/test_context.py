"""
Runs without any API keys — validates the traceback parser and AST-based
function extraction, which are the two things that must be bulletproof
before anything touches the LLM.
"""
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.context import parse_pytest_log, extract_enclosing_function

SAMPLE_LOG = textwrap.dedent(
    """
    ============================= test session starts ==============================
    collected 1 item

    tests/test_math.py F                                                     [100%]

    =================================== FAILURES ====================================
    ______________________________ test_divide ______________________________________

        def test_divide():
    >       assert divide(10, 0) == 0
    E       ZeroDivisionError: division by zero

    tests/test_math.py:6: ZeroDivisionError
    Traceback (most recent call last):
      File "tests/test_math.py", line 6, in test_divide
        assert divide(10, 0) == 0
      File "src/math_utils.py", line 2, in divide
        return a / b
    ZeroDivisionError: division by zero
    =========================== short test summary info ============================
    FAILED tests/test_math.py::test_divide - ZeroDivisionError: division by zero
    """
)


def test_parse_pytest_log():
    failure = parse_pytest_log(SAMPLE_LOG)
    assert failure is not None
    assert failure.test_name == "test_divide"
    assert failure.exception_type == "ZeroDivisionError"
    assert "division by zero" in failure.exception_message
    print("parse_pytest_log OK ->", failure)


def test_extract_enclosing_function(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    module = src_dir / "math_utils.py"
    module.write_text(
        textwrap.dedent(
            """
            def unrelated():
                return 1

            def divide(a, b):
                return a / b

            def also_unrelated():
                return 2
            """
        ).strip()
    )
    func_src = extract_enclosing_function(str(tmp_path), "src/math_utils.py", 5)
    assert "def divide(a, b):" in func_src
    assert "return a / b" in func_src
    assert "unrelated" not in func_src
    print("extract_enclosing_function OK ->\n", func_src)


if __name__ == "__main__":
    test_parse_pytest_log()
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_extract_enclosing_function(Path(d))
    print("\nAll smoke tests passed.")
