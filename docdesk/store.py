"""Store document text and normalized embedding vectors in PostgreSQL."""

from contextlib import contextmanager
from dataclasses import dataclass
import heapq
from uuid import uuid4

import numpy as np
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

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
    def __init__(self, database_url: str, embedding_model: str):
        self.embedding_model = embedding_model
        self.pool = ConnectionPool(
            database_url, min_size=1, max_size=6, timeout=5, open=False,
            kwargs={"row_factory": dict_row, "connect_timeout": 5},
        )

    def open(self):
        self.pool.open(wait=True, timeout=10)

    def close(self):
        self.pool.close()

    @contextmanager
    def connect(self):
        # The pool commits on success and rolls back on failure.
        with self.pool.connection() as connection:
            connection.execute("SET LOCAL statement_timeout = '5s'")
            yield connection

    @staticmethod
    def lock_writes(db):
        # A transaction lock serializes index changes across workers and replicas.
        db.execute("SELECT pg_advisory_xact_lock(62730501)")

    def initialize(self):
        with self.connect() as db:
            self.lock_writes(db)
            db.execute(f"""
                CREATE TABLE IF NOT EXISTS index_config (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    digest TEXT NOT NULL UNIQUE,
                    chunk_count INTEGER NOT NULL,
                    added_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    number INTEGER NOT NULL,
                    location TEXT NOT NULL,
                    text TEXT NOT NULL,
                    vector BYTEA NOT NULL CHECK(octet_length(vector) = {DIMENSIONS * 4}),
                    PRIMARY KEY (document_id, number)
                );
            """)
            # Vectors from different embedding models cannot share an index.
            signature = f"1/{self.embedding_model}/{DIMENSIONS}"
            db.execute(
                "INSERT INTO index_config VALUES ('embedding', %s) ON CONFLICT DO NOTHING",
                (signature,),
            )
            saved = db.execute("SELECT value FROM index_config WHERE name = 'embedding'").fetchone()
            if saved["value"] != signature:
                raise RuntimeError("The embedding model changed. Use an empty database and re-upload documents.")

    def ping(self):
        with self.connect() as db:
            db.execute("SELECT 1 FROM index_config LIMIT 1").fetchone()

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
            SELECT id, filename, chunk_count, added_at FROM documents WHERE digest = %s
        """, (digest,)).fetchone()
        return dict(row) if row else None

    def add_document(
        self, filename: str, digest: str, chunks: list[Chunk], vectors: list[np.ndarray],
    ) -> tuple[dict, bool]:
        with self.connect() as db:
            self.lock_writes(db)
            existing = self.document_by_digest(db, digest)
            if existing:
                return existing, True
            totals = db.execute("SELECT COUNT(*) AS documents, COALESCE(SUM(chunk_count), 0) AS chunks FROM documents").fetchone()
            if totals["documents"] >= MAX_DOCUMENTS or totals["chunks"] + len(chunks) > MAX_INDEX_CHUNKS:
                raise IndexFull("The library is full. Delete a document and try again.")
            document_id = uuid4().hex
            db.execute(
                "INSERT INTO documents (id, filename, digest, chunk_count) VALUES (%s, %s, %s, %s)",
                (document_id, filename, digest, len(chunks)),
            )
            # Each row keeps the actual text beside its vector and source location.
            rows = (
                (document_id, chunk.number, chunk.location, chunk.text, vector.tobytes())
                for chunk, vector in zip(chunks, vectors, strict=True)
            )
            with db.cursor() as cursor:
                cursor.executemany("INSERT INTO chunks VALUES (%s, %s, %s, %s, %s)", rows)
            return self.document_by_digest(db, digest), False

    def delete(self, document_id: str) -> bool:
        with self.connect() as db:
            self.lock_writes(db)
            # ON DELETE CASCADE also removes all chunks from future searches.
            result = db.execute("DELETE FROM documents WHERE id = %s", (document_id,))
            return result.rowcount == 1

    def has_documents(self, document_id: str | None) -> bool:
        with self.connect() as db:
            if document_id:
                return db.execute("SELECT 1 FROM documents WHERE id = %s", (document_id,)).fetchone() is not None
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
            sql += " WHERE c.document_id = %s"
            parameters = (document_id,)

        sql += " ORDER BY c.document_id, c.number"

        def qualifying_matches(rows):
            for row in rows:
                vector = np.frombuffer(row["vector"], dtype="<f4")
                score = float(np.dot(query, vector))
                if score >= threshold:
                    yield Match(row["filename"], row["location"], row["number"], row["text"], score)

        with self.connect() as db:
            # Scan every eligible vector, but keep only the best few results in memory.
            with db.cursor(name="docdesk_search") as cursor:
                cursor.execute(sql, parameters)
                return heapq.nlargest(top_k, qualifying_matches(cursor), key=lambda match: match.score)
