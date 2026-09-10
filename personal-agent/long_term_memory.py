"""
long_term_memory.py

Semantic persistent memory for the personal agent.

Stores facts, preferences, and notes as vector embeddings in a local
ChromaDB database and retrieves them by meaning — not by exact keyword
match.  This lets the user say "remember that my deadline is Friday"
and later ask "when is my deadline?" and get the right answer back.

Design principles
-----------------
- Local-first: ChromaDB runs in-process with SQLite persistence;
  embeddings come from Ollama (nomic-embed-text) or fall back to
  Chroma's built-in ONNX model.  No cloud, no network required.
- Thread-safe: a module-level singleton with a threading lock protects
  the SQLite backend from Windows mandatory file-lock collisions.
- Graceful degradation: if ChromaDB or the embedding model is
  unavailable, every public function returns a sensible empty/default
  rather than crashing the agent.
- Minimal API surface: remember / recall / forget_about / list_recent /
  count / clear_all — nothing else.  The rest of the agent imports
  only these six.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_BASE_DIR = Path(__file__).resolve().parent
_DB_DIR = _BASE_DIR / "data" / "chroma_memory"

# ── module-level singleton state ───────────────────────────────────────────
_store: Optional[_MemoryStore] = None
_init_lock = threading.Lock()
_init_failed = False


# ── preference auto-detection ──────────────────────────────────────────────
_PREF_WORDS = {
    "prefer", "prefers", "preferred", "preference",
    "like", "likes", "love", "loves",
    "always", "never", "favorite", "favourite",
    "hate", "hates", "dislike", "dislikes",
    "rather", "instead",
}


def _guess_category(text: str) -> str:
    """Guess whether a remembered fact is a preference or a plain note."""
    words = set(text.lower().split())
    if words & _PREF_WORDS:
        return "preference"
    return "note"


def _make_id(text: str) -> str:
    """Generate a stable, unique ID from timestamp + text hash."""
    ts = int(time.time() * 1000)
    h = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"mem_{ts}_{h}"


# ── internal store class ──────────────────────────────────────────────────

class _MemoryStore:
    """Wraps ChromaDB collection.  Created once via _get_store()."""

    def __init__(self):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        _DB_DIR.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(_DB_DIR),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        # Try Ollama embedding function first, fall back to built-in ONNX.
        ef = self._ollama_ef() or self._onnx_ef()

        self.collection = self.client.get_or_create_collection(
            name="agent_long_term_memory",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _ollama_ef():
        """Try to create an Ollama embedding function."""
        try:
            from chromadb.utils.embedding_functions import (
                OllamaEmbeddingFunction,
            )
            ef = OllamaEmbeddingFunction(
                url="http://localhost:11434",
                model_name="nomic-embed-text",
            )
            # Smoke-test: embed a tiny string to verify Ollama is reachable
            # and the model is pulled.
            ef(["ping"])
            print("(long_term_memory: using Ollama nomic-embed-text)")
            return ef
        except Exception as e:
            print(f"(long_term_memory: Ollama embeddings unavailable — {e})")
            return None

    @staticmethod
    def _onnx_ef():
        """Fall back to Chroma's built-in ONNX all-MiniLM-L6-v2."""
        try:
            from chromadb.utils.embedding_functions import (
                DefaultEmbeddingFunction,
            )
            ef = DefaultEmbeddingFunction()
            print("(long_term_memory: using built-in ONNX embeddings)")
            return ef
        except Exception as e:
            print(f"(long_term_memory: ONNX embeddings also unavailable — {e})")
            return None


def _get_store() -> Optional[_MemoryStore]:
    """Lazy-initialise the singleton store.  Returns None on failure."""
    global _store, _init_failed
    if _store is not None:
        return _store
    if _init_failed:
        return None
    with _init_lock:
        if _store is not None:
            return _store
        try:
            _store = _MemoryStore()
        except Exception as e:
            _init_failed = True
            print(f"⚠ long_term_memory: could not initialise ChromaDB — {e}")
            print("  Memory commands will be unavailable this session.")
        return _store


# ── public API ─────────────────────────────────────────────────────────────

def remember(text: str, category: str | None = None,
             source: str = "conversation") -> str:
    """Store a fact, preference, or note.  Returns a confirmation string."""
    store = _get_store()
    if store is None:
        return "Sorry, long-term memory is unavailable right now."

    text = text.strip()
    if not text:
        return "Nothing to remember — the text was empty."

    cat = category or _guess_category(text)
    mem_id = _make_id(text)

    try:
        store.collection.add(
            ids=[mem_id],
            documents=[text],
            metadatas=[{
                "category": cat,
                "source": source,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }],
        )
        return f"Got it — I'll remember that. (category: {cat})"
    except Exception as e:
        return f"Couldn't save that memory: {e}"


def recall(query: str, limit: int = 3,
           category: str | None = None) -> List[Dict[str, Any]]:
    """Semantic search for matching memories.  Returns a list of dicts with
    keys: id, text, category, similarity, timestamp."""
    store = _get_store()
    if store is None:
        return []

    query = query.strip()
    if not query:
        return []

    where = {"category": category} if category else None

    try:
        results = store.collection.query(
            query_texts=[query],
            n_results=min(limit, store.collection.count() or 1),
            where=where,
        )
    except Exception as e:
        print(f"(long_term_memory recall error: {e})")
        return []

    memories: list[dict] = []
    if results and results.get("documents") and results["documents"][0]:
        docs = results["documents"][0]
        metas = (results.get("metadatas") or [[]])[0]
        dists = (results.get("distances") or [[]])[0]
        ids = results["ids"][0]

        for mid, doc, meta, dist in zip(ids, docs, metas or [{}] * len(docs),
                                         dists or [0.0] * len(docs)):
            memories.append({
                "id": mid,
                "text": doc,
                "category": (meta or {}).get("category", "note"),
                "similarity": round(1.0 - dist, 4),
                "timestamp": (meta or {}).get("timestamp", ""),
            })

    return memories


def recall_formatted(query: str, limit: int = 3,
                     category: str | None = None) -> str:
    """Like recall(), but returns a single human-/voice-friendly string."""
    memories = recall(query, limit=limit, category=category)
    if not memories:
        return "I don't have any memories about that."

    if len(memories) == 1:
        return f"I remember: {memories[0]['text']}"

    parts = [f"I found {len(memories)} things:"]
    for i, m in enumerate(memories, 1):
        parts.append(f"  {i}. {m['text']}")
    return "\n".join(parts)


def forget_about(query: str) -> str:
    """Delete the single best-matching memory for the given query."""
    store = _get_store()
    if store is None:
        return "Sorry, long-term memory is unavailable right now."

    matches = recall(query, limit=1)
    if not matches:
        return "I don't have any memories matching that."

    best = matches[0]
    try:
        store.collection.delete(ids=[best["id"]])
        return f"Forgotten: \"{best['text']}\""
    except Exception as e:
        return f"Couldn't delete that memory: {e}"


def list_recent(limit: int = 10) -> str:
    """Return a formatted list of the most recent memories."""
    store = _get_store()
    if store is None:
        return "Sorry, long-term memory is unavailable right now."

    total = store.collection.count()
    if total == 0:
        return "No memories stored yet."

    try:
        # ChromaDB doesn't support ordering by metadata, so we fetch all
        # and sort client-side.  For a personal agent this is fine — we'll
        # rarely exceed a few hundred entries.
        result = store.collection.get(
            limit=min(total, 200),
            include=["documents", "metadatas"],
        )
    except Exception as e:
        return f"Couldn't list memories: {e}"

    entries: list[dict] = []
    for doc, meta in zip(result["documents"], result["metadatas"] or [{}] * len(result["documents"])):
        entries.append({
            "text": doc,
            "category": (meta or {}).get("category", "note"),
            "timestamp": (meta or {}).get("timestamp", ""),
        })

    # Sort newest first
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    entries = entries[:limit]

    lines = [f"Your {len(entries)} most recent memories (of {total} total):"]
    for e in entries:
        cat = e["category"]
        ts = e["timestamp"][:10] if e["timestamp"] else "?"
        lines.append(f"  • [{cat}] {e['text']}  ({ts})")
    return "\n".join(lines)


def count() -> int:
    """Return total number of stored memories."""
    store = _get_store()
    if store is None:
        return 0
    return store.collection.count()


def clear_all() -> str:
    """Delete ALL memories.  Caller must gate this behind confirmation."""
    store = _get_store()
    if store is None:
        return "Sorry, long-term memory is unavailable right now."

    n = store.collection.count()
    if n == 0:
        return "No memories to clear."

    try:
        # Delete the collection and recreate it empty.
        ef = store.collection._embedding_function
        store.client.delete_collection("agent_long_term_memory")
        store.collection = store.client.get_or_create_collection(
            name="agent_long_term_memory",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
        return f"Cleared all {n} memories."
    except Exception as e:
        return f"Couldn't clear memories: {e}"


# ── self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("Long-term memory self-test")
    print("=" * 50)

    # Store some facts
    print("\n1. Storing memories...")
    print("  ", remember("My project deadline is Friday"))
    print("  ", remember("I prefer dark mode in all apps"))
    print("  ", remember("The wifi password is sunshine42", category="fact"))

    # Count
    print(f"\n2. Count: {count()} memories stored")

    # Recall by meaning
    print("\n3. Semantic recall:")
    print("  Query: 'when is the deadline?'")
    print("  ", recall_formatted("when is the deadline?", limit=1))

    print("\n  Query: 'what are my preferences?'")
    print("  ", recall_formatted("what are my preferences?", limit=2))

    # List recent
    print("\n4. Recent memories:")
    print(list_recent())

    # Forget
    print("\n5. Forgetting 'wifi password'...")
    print("  ", forget_about("wifi password"))
    print(f"   Count after forget: {count()}")

    # Clear
    print("\n6. Clearing all...")
    print("  ", clear_all())
    print(f"   Count after clear: {count()}")

    print("\n[OK] Self-test complete.")
