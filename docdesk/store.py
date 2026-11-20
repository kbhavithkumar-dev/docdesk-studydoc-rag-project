"""Store document text and normalized embedding vectors in a local SQLite file."""

from contextlib import contextmanager
from dataclasses import dataclass
import heapq
from pathlib import Path
import sqlite3
from uuid import uuid4

import numpy as np

from .documents import Chunk
from .settings import DIMENSIONS, MAX_DOCUMENTS, MAX_INDEX_CHUNKS


class IndexFull(ValueError):
    """The current library has reached its configured capacity."""


@dataclass(frozen=True)
class Match:
    filename: str
    location: str
    chunk_number: int
    text: str
    score: float


class Store:
    def __init__(self, path: Path, embedding_model: str):
        self.path = path
        self.embedding_model = embedding_model

    @contextmanager
    def connect(self):
        # Separate connections let upload, search and delete use separate transactions.
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            # SQLite commits on success and rolls back on an exception.
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL").fetchone()
            db.executescript(f"""
                CREATE TABLE IF NOT EXISTS index_config (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    digest TEXT NOT NULL UNIQUE,
                    chunk_count INTEGER NOT NULL,
                    added_at TEXT NOT NULL DEFAULT
                        (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    number INTEGER NOT NULL,
                    location TEXT NOT NULL,
                    text TEXT NOT NULL,
                    vector BLOB NOT NULL CHECK(length(vector) = {DIMENSIONS * 4}),
                    PRIMARY KEY (document_id, number)
                );
            """)
            # Vectors from different embedding models cannot share an index.
            signature = f"1/{self.embedding_model}/{DIMENSIONS}"
            db.execute(
                "INSERT OR IGNORE INTO index_config VALUES ('embedding', ?)", (signature,),
            )
            saved = db.execute("SELECT value FROM index_config WHERE name = 'embedding'").fetchone()
            if saved["value"] != signature:
                raise RuntimeError("The embedding model changed. Use a new DATA_DIR and upload documents again.")

    def list_documents(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("""
                SELECT id, filename, chunk_count, added_at FROM documents
                ORDER BY added_at DESC, id
            """)]

    def find_duplicate(self, digest: str) -> dict | None:
        with self.connect() as db:
            return self.document_by_digest(db, digest)

    @staticmethod
    def document_by_digest(db, digest: str) -> dict | None:
        row = db.execute("""
            SELECT id, filename, chunk_count, added_at FROM documents WHERE digest = ?
        """, (digest,)).fetchone()
        return dict(row) if row else None

    def add_document(
        self, filename: str, digest: str, chunks: list[Chunk], vectors: list[np.ndarray],
    ) -> tuple[dict, bool]:
        with self.connect() as db:
            # Reserve the writer before checking duplicates or the capacity limit.
            db.execute("BEGIN IMMEDIATE")
            existing = self.document_by_digest(db, digest)
            if existing:
                return existing, True
            totals = db.execute("SELECT COUNT(*), COALESCE(SUM(chunk_count), 0) FROM documents").fetchone()
            if totals[0] >= MAX_DOCUMENTS or totals[1] + len(chunks) > MAX_INDEX_CHUNKS:
                raise IndexFull("The library is full. Delete a document and try again.")
            document_id = uuid4().hex
            db.execute(
                "INSERT INTO documents (id, filename, digest, chunk_count) VALUES (?, ?, ?, ?)",
                (document_id, filename, digest, len(chunks)),
            )
            # Each row keeps the actual text beside its vector and source location.
            rows = (
                (document_id, chunk.number, chunk.location, chunk.text, vector.tobytes())
                for chunk, vector in zip(chunks, vectors, strict=True)
            )
            db.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)", rows)
            return self.document_by_digest(db, digest), False

    def delete(self, document_id: str) -> bool:
        with self.connect() as db:
            # ON DELETE CASCADE also removes all chunks from future searches.
            result = db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            return result.rowcount == 1

    def has_documents(self, document_id: str | None) -> bool:
        with self.connect() as db:
            if document_id:
                return db.execute("SELECT 1 FROM documents WHERE id = ?", (document_id,)).fetchone() is not None
            return db.execute("SELECT 1 FROM documents LIMIT 1").fetchone() is not None

    def search(
        self, query: np.ndarray, *, document_id: str | None, threshold: float, top_k: int,
    ) -> list[Match]:
        sql = """
            SELECT d.filename, c.location, c.number, c.text, c.vector
            FROM chunks c JOIN documents d ON d.id = c.document_id
        """
        parameters = ()
        if document_id:
            sql += " WHERE c.document_id = ?"
            parameters = (document_id,)

        def qualifying_matches(rows):
            for row in rows:
                vector = np.frombuffer(row["vector"], dtype="<f4")
                score = float(np.dot(query, vector))
                if score >= threshold:
                    yield Match(row["filename"], row["location"], row["number"], row["text"], score)

        with self.connect() as db:
            # Scan every eligible vector, but keep only the best few results in memory.
            return heapq.nlargest(top_k, qualifying_matches(db.execute(sql, parameters)), key=lambda match: match.score)
