"""
Indexes a Python repo into ChromaDB at function/class granularity so the
fix-generation chain can retrieve related code the traceback doesn't show
directly (helper functions, imported classes, etc).
"""
from pathlib import Path

from langchain_community.document_loaders.generic import GenericLoader
from langchain_community.document_loaders.parsers import LanguageParser
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

from agent.config import settings

_EXCLUDE_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".chroma_store"}


def _iter_py_files(repo_path: str):
    for p in Path(repo_path).rglob("*.py"):
        if not any(part in _EXCLUDE_DIRS for part in p.parts):
            yield p


def build_or_update_index(repo_path: str = None, changed_files: list[str] | None = None):
    """
    Full index if changed_files is None, otherwise only re-embeds those files
    (cheap incremental update for a normal push).
    """
    repo_path = repo_path or settings.REPO_PATH
    embeddings = HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)

    vectordb = Chroma(
        collection_name="repo_code",
        embedding_function=embeddings,
        persist_directory=settings.CHROMA_PERSIST_DIR,
    )

    files = (
        [Path(repo_path) / f for f in changed_files]
        if changed_files
        else list(_iter_py_files(repo_path))
    )
    files = [f for f in files if f.exists() and f.suffix == ".py"]
    if not files:
        return vectordb

    loader = GenericLoader.from_filesystem(
        repo_path,
        suffixes=[".py"],
        parser=LanguageParser(language=Language.PYTHON, parser_threshold=0),
    )
    docs = [d for d in loader.load() if Path(d.metadata.get("source", "")) in files]

    splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.PYTHON, chunk_size=800, chunk_overlap=100
    )
    chunks = splitter.split_documents(docs)

    if changed_files:
        # crude incremental update: drop old chunks for these files, add new ones
        for f in changed_files:
            try:
                vectordb.delete(where={"source": str(Path(repo_path) / f)})
            except Exception:
                pass

    if chunks:
        vectordb.add_documents(chunks)

    return vectordb


def get_retriever(k: int = 5):
    embeddings = HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)
    vectordb = Chroma(
        collection_name="repo_code",
        embedding_function=embeddings,
        persist_directory=settings.CHROMA_PERSIST_DIR,
    )
    return vectordb.as_retriever(search_kwargs={"k": k})
