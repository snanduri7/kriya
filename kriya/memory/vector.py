import asyncio
import logging
import os
import re
import sqlite3
import struct
from typing import Any, Dict, List, NamedTuple, Optional

import click
import httpx
import numpy as np

from kriya.core.db import get_connection

logger = logging.getLogger(__name__)

class OllamaEmbeddingClient:
    """Queries OpenAI-compatible or local Ollama endpoints for vector embeddings."""

    def __init__(self, base_url: str, model: str, *, egress_policy: str) -> None:
        """``egress_policy`` is required (autonomy.egress_policy): embedding
        requests carry repository code and goal text, so they obey the same
        local_only boundary as LLMClient (PRD-012). No default - a caller
        cannot construct an ungoverned client by omission."""
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.egress_policy = egress_policy
        self.detected_dimensions = 768

    def _enforce_egress(self) -> None:
        """Raised before any request and never swallowed by the graceful
        dummy-vector degradation below: a refused endpoint is a boundary,
        not an outage."""
        if self.egress_policy == "local_only":
            from kriya.core.llm import EgressViolationError, is_local_url

            if not is_local_url(self.base_url):
                raise EgressViolationError(
                    f"Egress violation: embedding request to external endpoint '{self.base_url}' "
                    "blocked under 'local_only' policy."
                )

    async def get_embedding(self, text: str, client: Optional[httpx.AsyncClient] = None, is_query: bool = False) -> List[float]:
        """Fetch embedding vector for the given text segment."""
        self._enforce_egress()
        if "nomic" in self.model.lower():
            prefix = "search_query: " if is_query else "search_document: "
            if not text.startswith(prefix):
                text = prefix + text
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client_instance:
                    emb = await self._get_embedding_with_client(text, client_instance)
            else:
                emb = await self._get_embedding_with_client(text, client)
            if emb:
                self.detected_dimensions = len(emb)
            return emb
        except Exception as e:
            logger.error(f"Failed to fetch embedding: {e}", exc_info=True)
            # Return dummy vector if model is not running to degrade gracefully
            return [0.0] * self.detected_dimensions

    async def _get_embedding_with_client(self, text: str, client: httpx.AsyncClient) -> List[float]:
        # Attempt standard OpenAI /v1/embeddings format
        url = f"{self.base_url}/embeddings"
        payload = {
            "input": text,
            "model": self.model
        }
        headers = {"Content-Type": "application/json"}
        
        resp = await client.post(url, json=payload, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json()
            return data["data"][0]["embedding"]
            
        # Fallback to Ollama native /api/embeddings if base_url is root
        ollama_root = self.base_url.replace("/v1", "")
        url_fallback = f"{ollama_root}/api/embeddings"
        payload_fallback = {
            "model": self.model,
            "prompt": text
        }
        
        resp_fb = await client.post(url_fallback, json=payload_fallback)
        resp_fb.raise_for_status()
        data_fb = resp_fb.json()
        return data_fb["embedding"]

    async def get_embeddings(self, texts: List[str], is_query: bool = False) -> List[List[float]]:
        """Fetch multiple embedding vectors concurrently and batched to optimize performance."""
        if not texts:
            return []
        self._enforce_egress()

        batch_size = 32
        batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
        
        all_embeddings = []
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            for batch in batches:
                processed_batch = []
                for text in batch:
                    if "nomic" in self.model.lower():
                        prefix = "search_query: " if is_query else "search_document: "
                        if not text.startswith(prefix):
                            text = prefix + text
                    processed_batch.append(text)
                
                try:
                    # Attempt standard OpenAI batch format
                    url = f"{self.base_url}/embeddings"
                    payload = {
                        "input": processed_batch,
                        "model": self.model
                    }
                    headers = {"Content-Type": "application/json"}
                    
                    resp = await client.post(url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        batch_embs = [item["embedding"] for item in data["data"]]
                        if batch_embs:
                            self.detected_dimensions = len(batch_embs[0])
                        all_embeddings.extend(batch_embs)
                        continue
                    
                    # Fallback to concurrent single fetches
                    ollama_root = self.base_url.replace("/v1", "")
                    url_fallback = f"{ollama_root}/api/embeddings"
                    
                    async def fetch_single_fallback(t, url_fallback=url_fallback):
                        payload_fb = {
                            "model": self.model,
                            "prompt": t
                        }
                        resp_fb = await client.post(url_fallback, json=payload_fb)
                        resp_fb.raise_for_status()
                        emb = resp_fb.json()["embedding"]
                        self.detected_dimensions = len(emb)
                        return emb
                    
                    fallback_tasks = [fetch_single_fallback(t) for t in processed_batch]
                    batch_embs = await asyncio.gather(*fallback_tasks)
                    all_embeddings.extend(batch_embs)
                except Exception as e:
                    logger.error(f"Failed to fetch batch embedding: {e}", exc_info=True)
                    all_embeddings.extend([[0.0] * self.detected_dimensions for _ in processed_batch])
                    
        return all_embeddings


def serialize_embedding(vector: List[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)

def deserialize_embedding(blob: bytes) -> List[float]:
    num_floats = len(blob) // 4
    return list(struct.unpack(f"{num_floats}f", blob))

# Short English function words that carry no retrieval signal in a goal.
_LEXICAL_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "onto", "that", "this", "these", "those", "than",
    "then", "when", "where", "which", "while", "should", "would", "could", "must", "not", "are",
    "was", "were", "has", "have", "had", "its", "their", "them", "they", "any", "all", "each",
    "instead", "before", "after", "only", "also", "some", "such", "via", "per", "but", "our", "you",
})
_LEXICAL_TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_MAX_LEXICAL_TERMS = 32
_LIKE_FALLBACK_TERMS = 8


def lexical_query_terms(query_text: str) -> List[str]:
    """Distinct lower-cased lexical terms of ``query_text``: each identifier
    as written and its camelCase/snake_case parts, in first-seen order."""
    terms: List[str] = []
    seen = set()
    for token in _LEXICAL_TERM_RE.findall(query_text or ""):
        for candidate in [token, *split_camel_snake(token).split()]:
            term = candidate.lower()
            if len(term) >= 3 and term not in _LEXICAL_STOPWORDS and term not in seen:
                seen.add(term)
                terms.append(term)
    return terms[:_MAX_LEXICAL_TERMS]


def split_camel_snake(text: str) -> str:
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1 \2', text)
    s2 = re.sub('([a-z0-9])([A-Z])', r'\1 \2', s1)
    return s2.replace('_', ' ').replace('-', ' ')

class SQLiteMetadataDict:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.conn = get_connection(self.db_path)
        self.init_table()

    def init_table(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_metadata (
                filepath TEXT PRIMARY KEY,
                mtime REAL,
                hash TEXT
            )
        """)
        try:
            cursor.execute("ALTER TABLE file_metadata ADD COLUMN hash TEXT")
        except Exception:
            pass
        self.conn.commit()

    def __getitem__(self, key: str) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT mtime, hash FROM file_metadata WHERE filepath = ?", (key,))
        row = cursor.fetchone()
        if row is None:
            raise KeyError(key)
        return {"mtime": row[0], "hash": row[1]}

    def __setitem__(self, key: str, value: Dict[str, Any]) -> None:
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO file_metadata (filepath, mtime, hash)
            VALUES (?, ?, ?)
        """, (key, value.get("mtime", 0.0), value.get("hash", "")))
        self.conn.commit()

    def __delitem__(self, key: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM file_metadata WHERE filepath = ?", (key,))
        self.conn.commit()

    def __contains__(self, key: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM file_metadata WHERE filepath = ?", (key,))
        row = cursor.fetchone()
        return row is not None

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self) -> List[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT filepath FROM file_metadata")
        rows = cursor.fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        if hasattr(self, "conn") and self.conn:
            self.conn.close()

class LearnedKnowledgeMatches(NamedTuple):
    """query_learned_knowledge's answer: the scored matches, plus the rows
    it could not use (undecodable, or embedded by another model)."""

    matches: List[Dict[str, Any]]
    malformed_rows: int
    other_embedding_rows: int


class LocalVectorStore:
    """SQLite-backed local vector store."""

    def __init__(self, index_path: str) -> None:
        if index_path.endswith(".json"):
            index_path = index_path[:-5] + ".db"
        self.db_path = os.path.abspath(index_path)
        self.use_fts = True
        self.init_db()
        self.file_metadata = SQLiteMetadataDict(self.db_path)

    def init_db(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = get_connection(self.db_path)
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vector_chunks (
                filepath TEXT,
                chunk_index INTEGER,
                text TEXT,
                embedding BLOB,
                model_name TEXT,
                dimensions INTEGER,
                PRIMARY KEY (filepath, chunk_index)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS learned_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT,
                embedding BLOB,
                model_name TEXT,
                dimensions INTEGER,
                provenance_url TEXT,
                fetch_date TEXT
            )
        """)
        
        self.use_fts = True
        # Check if fts_chunks table already exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fts_chunks'")
        if not cursor.fetchone():
            try:
                cursor.execute("""
                    CREATE VIRTUAL TABLE fts_chunks USING fts5(
                        filepath,
                        chunk_index,
                        text,
                        split_text
                    )
                """)
            except sqlite3.OperationalError:
                self.use_fts = False
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS fts_chunks_fallback (
                        filepath TEXT,
                        chunk_index INTEGER,
                        text TEXT,
                        split_text TEXT,
                        PRIMARY KEY (filepath, chunk_index)
                    )
                """)
        self.conn.commit()

    def verify_model(self, model_name: str, dimensions: int) -> None:
        """Confirms the vector index was built with the model/dimensions the
        caller now expects. Previously checked only ONE arbitrary row
        (LIMIT 1) - a partially-mismatched index (e.g. after the zero-
        vector-on-embedding-failure bug, or a mid-migration model switch)
        could pass this check while still containing rows from a DIFFERENT
        model/dimensions, which query() then silently drops with no warning
        at all (2026-08-12 SME review). Now checks every DISTINCT (model,
        dimensions) combination actually present: raises only if NONE of
        them match (the original hard-fail case, re-indexing is required
        either way); warns instead if some rows match and others don't (a
        mixed index still has usable content, but the mismatched rows are
        being silently skipped)."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT model_name, dimensions FROM vector_chunks")
        rows = cursor.fetchall()
        if not rows:
            return
        matching = [(m, d) for m, d in rows if m == model_name and d == dimensions]
        mismatched = [(m, d) for m, d in rows if m != model_name or d != dimensions]
        if mismatched and not matching:
            db_model, db_dims = mismatched[0]
            raise ValueError(
                f"Index model mismatch: Database index was built with model '{db_model}' (dim: {db_dims}), "
                f"but configuration specifies model '{model_name}' (dim: {dimensions}). "
                f"Please re-index the repository using 'kriya analyze'."
            )
        elif mismatched:
            logger.warning(
                f"Vector index at '{self.db_path}' contains rows from {len(mismatched)} other "
                f"model/dimension combination(s) besides the configured '{model_name}' (dim: {dimensions}) "
                f"- those rows will be silently skipped during search. Consider re-indexing with "
                f"'kriya analyze --force'."
            )

    def load(self) -> None:
        pass

    def save(self) -> None:
        if hasattr(self, "conn") and self.conn:
            self.conn.commit()

    def close(self) -> None:
        if hasattr(self, "file_metadata") and self.file_metadata:
            self.file_metadata.close()
        if hasattr(self, "conn") and self.conn:
            self.conn.close()

    @property
    def documents(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT filepath, chunk_index, text, embedding FROM vector_chunks")
        rows = cursor.fetchall()
        
        docs = []
        for filepath, chunk_index, text, blob in rows:
            docs.append({
                "filepath": filepath,
                "chunk_index": chunk_index,
                "text": text,
                "embedding": deserialize_embedding(blob)
            })
        return docs

    def remove_file(self, filepath: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM vector_chunks WHERE filepath = ?", (filepath,))
        if self.use_fts:
            cursor.execute("DELETE FROM fts_chunks WHERE filepath = ?", (filepath,))
        else:
            cursor.execute("DELETE FROM fts_chunks_fallback WHERE filepath = ?", (filepath,))
        self.conn.commit()

    def add_document(self, filepath: str, text: str, embedding: List[float], chunk_index: int = 0, model_name: str = "default", dimensions: int = 768) -> None:
        # Re-raise to prevent silent index wipe on model/dim mismatch
        self.verify_model(model_name, dimensions)

        cursor = self.conn.cursor()
        blob = serialize_embedding(embedding)
        cursor.execute("""
            INSERT OR REPLACE INTO vector_chunks (filepath, chunk_index, text, embedding, model_name, dimensions)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (filepath, chunk_index, text, blob, model_name, dimensions))
        
        split_val = split_camel_snake(text)
        if self.use_fts:
            cursor.execute("DELETE FROM fts_chunks WHERE filepath = ? AND chunk_index = ?", (filepath, chunk_index))
            cursor.execute("""
                INSERT INTO fts_chunks (filepath, chunk_index, text, split_text)
                VALUES (?, ?, ?, ?)
            """, (filepath, chunk_index, text, split_val))
        else:
            cursor.execute("""
                INSERT OR REPLACE INTO fts_chunks_fallback (filepath, chunk_index, text, split_text)
                VALUES (?, ?, ?, ?)
            """, (filepath, chunk_index, text, split_val))
            
        self.conn.commit()

    def query(self, query_embedding: List[float], top_k: int = 5, model_name: str = "default", dimensions: int = 768) -> List[Dict[str, Any]]:
        if not query_embedding:
            return []

        try:
            self.verify_model(model_name, dimensions)
        except ValueError as e:
            click.secho(f"Warning: {e}. Degrading query to lexical-only FTS matching.", fg="yellow", bold=True, err=True)
            return []

        cursor = self.conn.cursor()
        cursor.execute("SELECT filepath, chunk_index, text, embedding FROM vector_chunks")
        rows = cursor.fetchall()

        if not rows:
            return []

        docs = []
        embeddings = []
        for filepath, chunk_index, text, blob in rows:
            doc_emb = deserialize_embedding(blob)
            if len(doc_emb) == len(query_embedding):
                docs.append({
                    "filepath": filepath,
                    "chunk_index": chunk_index,
                    "text": text
                })
                embeddings.append(doc_emb)

        if not embeddings:
            return []

        # Vectorized cosine similarity using NumPy
        q_vec = np.array(query_embedding, dtype=np.float32)
        doc_matrix = np.array(embeddings, dtype=np.float32)

        dot_products = np.dot(doc_matrix, q_vec)
        q_norm = np.linalg.norm(q_vec)
        doc_norms = np.linalg.norm(doc_matrix, axis=1)

        norms = q_norm * doc_norms
        scores = np.zeros_like(dot_products)
        valid = norms > 0
        scores[valid] = dot_products[valid] / norms[valid]

        results = []
        for doc, score in zip(docs, scores, strict=True):
            doc["score"] = float(score)
            results.append(doc)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def add_learned_knowledge(self, text: str, embedding: List[float], model_name: str = "default", dimensions: int = 768, provenance_url: str = "", fetch_date: str = "") -> None:
        cursor = self.conn.cursor()
        blob = serialize_embedding(embedding)
        cursor.execute("""
            INSERT INTO learned_knowledge (text, embedding, model_name, dimensions, provenance_url, fetch_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (text, blob, model_name, dimensions, provenance_url, fetch_date))
        self.conn.commit()

    def query_learned_knowledge(self, query_embedding: List[float], top_k: int = 5, model_name: str = "default", dimensions: int = 768) -> "LearnedKnowledgeMatches":
        """The ``top_k`` learned chunks most similar to ``query_embedding``,
        among rows embedded by ``model_name`` at ``dimensions`` only: a
        vector from another embedding model is not comparable, whatever its
        length (KNOWLEDGE-READPATH-001). Rows whose stored vector cannot be
        decoded, or disagrees with its own declared dimensions, are left
        out and counted, never scored."""
        if not query_embedding:
            return LearnedKnowledgeMatches([], 0, 0)

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT text, embedding, provenance_url, fetch_date FROM learned_knowledge "
            "WHERE model_name IS ? AND dimensions IS ?",
            (model_name, dimensions),
        )
        rows = cursor.fetchall()
        cursor.execute(
            "SELECT COUNT(*) FROM learned_knowledge WHERE NOT (model_name IS ? AND dimensions IS ?)",
            (model_name, dimensions),
        )
        other_embedding_rows = cursor.fetchone()[0]

        docs = []
        embeddings = []
        malformed_rows = 0
        for text, blob, url, date in rows:
            try:
                doc_emb = deserialize_embedding(blob)
            except (TypeError, struct.error):
                malformed_rows += 1
                continue
            if len(doc_emb) != dimensions or not isinstance(text, str):
                malformed_rows += 1
                continue
            docs.append({"text": text, "provenance_url": url, "fetch_date": date})
            embeddings.append(doc_emb)

        if not embeddings:
            return LearnedKnowledgeMatches([], malformed_rows, other_embedding_rows)

        # Vectorized cosine similarity using NumPy
        q_vec = np.array(query_embedding, dtype=np.float32)
        doc_matrix = np.array(embeddings, dtype=np.float32)

        dot_products = np.dot(doc_matrix, q_vec)
        q_norm = np.linalg.norm(q_vec)
        doc_norms = np.linalg.norm(doc_matrix, axis=1)

        norms = q_norm * doc_norms
        scores = np.zeros_like(dot_products)
        valid = norms > 0
        scores[valid] = dot_products[valid] / norms[valid]

        for doc, score in zip(docs, scores, strict=True):
            doc["score"] = float(score)
        docs.sort(key=lambda x: x["score"], reverse=True)
        return LearnedKnowledgeMatches(docs[:top_k], malformed_rows, other_embedding_rows)

    def remove_learned_knowledge(self, provenance_url: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM learned_knowledge WHERE provenance_url = ?", (provenance_url,))
        self.conn.commit()

    def query_lexical(self, query_text: str, top_k: int = 20) -> List[Dict[str, Any]]:
        """BM25-ranked lexical match of the query's distinct terms (PRD-027).

        Before PRD-027 this matched the ENTIRE query text as one FTS phrase,
        so a natural-language goal essentially never matched and the lexical
        leg of query_hybrid() contributed nothing; exact identifiers named
        in a goal (``OrderService.computeDiscount``) were lost to it. Terms
        are the query's identifiers and their camelCase/snake_case parts,
        minus short function words, OR-ed together."""
        terms = lexical_query_terms(query_text)
        if not terms:
            return []

        cursor = self.conn.cursor()
        results = []
        try:
            if self.use_fts:
                cursor.execute("""
                    SELECT filepath, chunk_index, text
                    FROM fts_chunks
                    WHERE fts_chunks MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (" OR ".join(f'"{term}"' for term in terms), top_k))
            else:
                like_terms = terms[:_LIKE_FALLBACK_TERMS]
                where = " OR ".join("lower(text) LIKE ? OR lower(split_text) LIKE ?" for _ in like_terms)
                params: List[Any] = []
                for term in like_terms:
                    params.extend((f"%{term}%", f"%{term}%"))
                cursor.execute(
                    f"SELECT filepath, chunk_index, text FROM fts_chunks_fallback WHERE {where} LIMIT ?",
                    (*params, top_k),
                )
            rows = cursor.fetchall()
            for filepath, chunk_index, text in rows:
                results.append({
                    "filepath": filepath,
                    "chunk_index": chunk_index,
                    "text": text
                })
        except Exception as e:
            logger.warning(f"Lexical query failed: {e}")
        return results

    def query_hybrid(self, query_text: str, query_embedding: List[float], top_k: int = 5, model_name: str = "default", dimensions: int = 768) -> List[Dict[str, Any]]:
        vector_results = self.query(query_embedding, top_k=top_k * 4, model_name=model_name, dimensions=dimensions)
        lexical_results = self.query_lexical(query_text, top_k=top_k * 4)

        # PRD027-PRECISION-001: each hit also carries its rank in each leg
        # (None when that leg did not return it) and how many valid top_k
        # hits each leg produced, so graph expansion can tell a corroborated
        # hit from a single-leg one. A vector hit is valid only with a
        # positive cosine (read here, before the RRF score replaces it); a
        # lexical hit always matched at least one query term.
        vector_ranks = {
            (res["filepath"], res["chunk_index"]): rank
            for rank, res in enumerate(vector_results, 1) if res.get("score", 0.0) > 0.0
        }
        lexical_ranks = {}
        for rank, res in enumerate(lexical_results, 1):
            lexical_ranks.setdefault((res["filepath"], res["chunk_index"]), rank)
        vector_valid_hits = sum(1 for rank in vector_ranks.values() if rank <= top_k)
        lexical_valid_hits = sum(1 for rank in lexical_ranks.values() if rank <= top_k)

        rrf_scores = {}
        chunk_map = {}
        k = 60
        
        for rank, res in enumerate(vector_results, 1):
            key = (res["filepath"], res["chunk_index"])
            rrf_scores[key] = rrf_scores.get(key, 0.0) + (1.0 / (k + rank))
            chunk_map[key] = res
            
        for rank, res in enumerate(lexical_results, 1):
            key = (res["filepath"], res["chunk_index"])
            rrf_scores[key] = rrf_scores.get(key, 0.0) + (1.0 / (k + rank))
            if key not in chunk_map:
                chunk_map[key] = res
                
        sorted_keys = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        
        hybrid_results = []
        for key in sorted_keys[:top_k]:
            res = chunk_map[key]
            res["score"] = rrf_scores[key]
            res["vector_rank"] = vector_ranks.get(key)
            res["lexical_rank"] = lexical_ranks.get(key)
            res["vector_valid_hits"] = vector_valid_hits
            res["lexical_valid_hits"] = lexical_valid_hits
            hybrid_results.append(res)
            
        return hybrid_results
