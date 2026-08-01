import asyncio
import logging
import os
import re
import sqlite3
import struct
from typing import Any, Dict, List, Optional

import click
import httpx
import numpy as np

from kriya.core.db import get_connection

logger = logging.getLogger(__name__)

class OllamaEmbeddingClient:
    """Queries OpenAI-compatible or local Ollama endpoints for vector embeddings."""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.detected_dimensions = 768

    async def get_embedding(self, text: str, client: Optional[httpx.AsyncClient] = None, is_query: bool = False) -> List[float]:
        """Fetch embedding vector for the given text segment."""
        if "nomic" in self.model.lower():
            prefix = "search_query: " if is_query else "search_document: "
            if not text.startswith(prefix):
                text = prefix + text
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=30.0) as client_instance:
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

        batch_size = 32
        batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
        
        all_embeddings = []
        async with httpx.AsyncClient(timeout=30.0) as client:
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
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT model_name, dimensions FROM vector_chunks LIMIT 1")
        row = cursor.fetchone()
        if row:
            db_model, db_dims = row[0], row[1]
            if db_model != model_name or db_dims != dimensions:
                raise ValueError(
                    f"Index model mismatch: Database index was built with model '{db_model}' (dim: {db_dims}), "
                    f"but configuration specifies model '{model_name}' (dim: {dimensions}). "
                    f"Please re-index the repository using 'kriya analyze'."
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

    def query_learned_knowledge(self, query_embedding: List[float], top_k: int = 5, model_name: str = "default", dimensions: int = 768) -> List[Dict[str, Any]]:
        if not query_embedding:
            return []

        cursor = self.conn.cursor()
        cursor.execute("SELECT text, embedding, provenance_url, fetch_date FROM learned_knowledge")
        rows = cursor.fetchall()

        if not rows:
            return []

        docs = []
        embeddings = []
        for text, blob, url, date in rows:
            doc_emb = deserialize_embedding(blob)
            if len(doc_emb) == len(query_embedding):
                docs.append({
                    "text": text,
                    "provenance_url": url,
                    "fetch_date": date
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

    def remove_learned_knowledge(self, provenance_url: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM learned_knowledge WHERE provenance_url = ?", (provenance_url,))
        self.conn.commit()

    def query_lexical(self, query_text: str, top_k: int = 20) -> List[Dict[str, Any]]:
        if not query_text:
            return []
            
        cursor = self.conn.cursor()
        results = []
        try:
            if self.use_fts:
                clean_query = query_text.replace('"', ' ').replace("'", " ")
                query_parts = f'"{clean_query}" OR "{split_camel_snake(clean_query)}"'
                cursor.execute("""
                    SELECT filepath, chunk_index, text
                    FROM fts_chunks
                    WHERE fts_chunks MATCH ?
                    LIMIT ?
                """, (query_parts, top_k))
            else:
                cursor.execute("""
                    SELECT filepath, chunk_index, text
                    FROM fts_chunks_fallback
                    WHERE text LIKE ? OR split_text LIKE ?
                    LIMIT ?
                """, (f"%{query_text}%", f"%{query_text}%", top_k))
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
            hybrid_results.append(res)
            
        return hybrid_results
