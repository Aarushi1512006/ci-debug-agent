"""
The core reasoning step: given a failure + retrieved context, ask Mistral
for a structured FixResponse. Uses with_structured_output so we get a
validated Pydantic object back directly, no manual JSON parsing.
"""
from langchain_core.prompts import ChatPromptTemplate
from langchain_mistralai import ChatMistralAI

from agent.config import settings
from agent.schemas import FixResponse, RefactorSuggestion
from agent.indexer import get_retriever

_FIX_SYSTEM_PROMPT = """You are a senior Python engineer fixing a failing test in CI.
Rules:
- Propose the SMALLEST possible change that fixes the failure. Do not refactor unrelated code.
- `original_snippet` must be an exact, verbatim substring of the given function source,
  so it can be located and replaced programmatically. Do not paraphrase it.
- If the fix would require touching CI config, secrets, or workflow files, or you are
  not confident a code change can fix this, set is_safe_to_apply to false and explain why.
- Give a realistic confidence score; do not default to 0.9.
"""

_FIX_USER_PROMPT = """## Failing test
{test_name}

## Exception
{exception_type}: {exception_message}

## Full traceback
{traceback_text}

## Function containing the failure ({file_path})
```python
{function_source}
```

## Related code retrieved from the repo (may or may not be relevant)
{retrieved_context}
"""


def _get_llm():
    return ChatMistralAI(
        model=settings.MISTRAL_MODEL,
        api_key=settings.MISTRAL_API_KEY,
        temperature=settings.LLM_TEMPERATURE,
    )


def generate_fix(
    test_name: str,
    exception_type: str,
    exception_message: str,
    traceback_text: str,
    file_path: str,
    function_source: str,
    retry_context: str = "",
) -> FixResponse:
    retriever = get_retriever(k=4)
    query = f"{exception_type} {exception_message} {test_name}"
    retrieved_docs = retriever.invoke(query)
    retrieved_context = "\n\n".join(
        f"# {d.metadata.get('source', 'unknown')}\n{d.page_content}" for d in retrieved_docs
    ) or "(no related context retrieved)"

    if retry_context:
        retrieved_context += f"\n\n## Previous attempt failed with:\n{retry_context}"

    llm = _get_llm().with_structured_output(FixResponse)
    prompt = ChatPromptTemplate.from_messages(
        [("system", _FIX_SYSTEM_PROMPT), ("user", _FIX_USER_PROMPT)]
    )
    chain = prompt | llm

    result: FixResponse = chain.invoke(
        {
            "test_name": test_name,
            "exception_type": exception_type,
            "exception_message": exception_message,
            "traceback_text": traceback_text,
            "file_path": file_path,
            "function_source": function_source,
            "retrieved_context": retrieved_context,
        }
    )
    return result


_REFACTOR_SYSTEM_PROMPT = """You are a senior Python engineer reviewing code for a refactor
suggestion (NOT a bug fix). Suggest improvements only where static analysis flagged an
issue (complexity, duplication, style). Never suggest behavior changes. These suggestions
will be posted as review comments only -- they will NOT be auto-applied."""


def generate_refactor_suggestion(file_path: str, issue: str, code_snippet: str) -> RefactorSuggestion:
    llm = _get_llm().with_structured_output(RefactorSuggestion)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _REFACTOR_SYSTEM_PROMPT),
            (
                "user",
                "File: {file_path}\nStatic analysis flagged: {issue}\n\n"
                "```python\n{code_snippet}\n```",
            ),
        ]
    )
    chain = prompt | llm
    return chain.invoke({"file_path": file_path, "issue": issue, "code_snippet": code_snippet})
