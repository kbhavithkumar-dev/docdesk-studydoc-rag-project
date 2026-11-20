"""Run with python -m unittest discover -s tests -v. No API key is required."""

from dataclasses import replace
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import json
import os
from uuid import uuid4
import unittest

from docx import Document
from fastapi.testclient import TestClient
import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject, DictionaryObject, NameObject,
)

from docdesk.documents import make_chunks
from docdesk.main import create_app
from docdesk.settings import DIMENSIONS, MAX_UPLOAD_BYTES, Settings
from docdesk.store import Store
from scripts.import_sqlite import copy_index

HEADERS = {"X-DocDesk-Request": "1"}
BOOK = "Students may borrow up to four books."
HOURS = "The observatory closes at midnight."


class FakeGemini:
    """Reply with small, predictable vectors inside the real Gemini REST response shape."""

    def __init__(self):
        self.calls = []
        self.quota_error = False
        self.fake_quote = False

    def __call__(self, request):
        assert request.headers["x-goog-api-key"] == "test-key"
        assert request.url.host == "generativelanguage.googleapis.com"
        payload = json.loads(request.content)
        self.calls.append(request.url.path)
        if self.quota_error:
            return httpx.Response(429)
        if request.url.path.endswith(":batchEmbedContents"):
            embeddings = []
            for item in payload["requests"]:
                text = item["content"]["parts"][0]["text"].lower()
                assert item["taskType"] in {"RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"}
                assert item["outputDimensionality"] == DIMENSIONS
                values = [0.0] * DIMENSIONS
                axis = 0 if "book" in text else 1 if "observatory" in text else 2
                values[axis] = 1.0
                embeddings.append({"values": values})
            return httpx.Response(200, json={"embeddings": embeddings})
        assert request.url.path.endswith(":generateContent")
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        user_data = json.loads(payload["contents"][0]["parts"][0]["text"])
        best = user_data["evidence"][0]
        quote = "This quote was invented." if self.fake_quote else best["text"]
        draft = {"statements": [{
            "text": best["text"], "source_id": best["source_id"], "quote": quote,
        }]}
        return httpx.Response(200, json={"candidates": [{
            "finishReason": "STOP", "content": {"parts": [{"text": json.dumps(draft)}]},
        }]})


TEST_DATABASE_URL = os.getenv("DOCDESK_TEST_DATABASE_URL")


@contextmanager
def isolated_schema():
    # A unique schema per test avoids deleting any existing user tables.
    schema = "docdesk_test_" + uuid4().hex
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            yield make_conninfo(TEST_DATABASE_URL, options=f"-c search_path={schema}")
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@unittest.skipUnless(TEST_DATABASE_URL, "Set DOCDESK_TEST_DATABASE_URL to test against PostgreSQL")
class AppTests(unittest.TestCase):
    def setUp(self):
        database_url = self.enterContext(isolated_schema())
        self.settings = Settings(database_url=database_url, api_key="test-key")
        self.fake = FakeGemini()
        self.client = self.enterContext(self.session(self.settings))

    def session(self, settings):
        app = create_app(settings, transport=httpx.MockTransport(self.fake))
        return TestClient(app, base_url="http://localhost", headers=HEADERS)

    def upload(self, filename="policy.txt", text=BOOK):
        return self.client.post("/api/documents", files={"file": (filename, text)})

    def ask(self, question, document_id=None):
        return self.client.post("/api/ask", json={"question": question, "document_id": document_id})

    def test_index_search_cite_delete(self):
        created = self.upload()
        self.assertEqual(created.status_code, 201)
        doc_id = created.json()["document"]["id"]
        self.upload("hours.txt", HOURS)
        response = self.ask("How many books can a student borrow?")
        self.assertEqual(response.status_code, 200)
        citation = response.json()["statements"][0]
        self.assertEqual(citation["filename"], "policy.txt")
        self.assertEqual(citation["quote"], BOOK)
        self.assertEqual(self.client.delete(f"/api/documents/{doc_id}").status_code, 204)
        self.assertEqual(self.ask("How many books?").json()["statements"], [])
        self.assertEqual(self.ask("How many books?", doc_id).status_code, 404)

    def test_scope_filters_other_documents(self):
        first = self.upload().json()["document"]["id"]
        self.upload("hours.txt", HOURS)
        self.assertEqual(self.ask("When does the observatory close?", first).json()["statements"], [])
        self.assertEqual(self.ask("When does the observatory close?").json()["statements"][0]["quote"], HOURS)

    def test_duplicate_reuses_existing_index(self):
        original = self.upload().json()["document"]
        repeated = self.upload("same-content.txt")
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(repeated.json()["duplicate"])
        self.assertEqual(repeated.json()["document"], original)
        self.assertEqual(len(self.fake.calls), 1)

    def test_readiness_checks_the_database(self):
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/readyz").json(), {"status": "ready"})

    def test_database_survives_new_app(self):
        document = self.upload().json()["document"]
        with self.session(self.settings) as restarted:
            self.assertEqual(restarted.get("/api/documents").json()["documents"], [document])
            result = restarted.post("/api/ask", json={"question": "How many books?"}).json()
            self.assertEqual(result["statements"][0]["quote"], BOOK)

    def test_migrate_sqlite_index_without_calling_gemini(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "docdesk.sqlite3"
            old = sqlite3.connect(source)
            try:
                old.executescript("""
                    CREATE TABLE index_config (name TEXT, value TEXT);
                    CREATE TABLE documents (id TEXT, filename TEXT, digest TEXT, chunk_count INT, added_at TEXT);
                    CREATE TABLE chunks (document_id TEXT, number INT, location TEXT, text TEXT, vector BLOB);
                    INSERT INTO index_config VALUES ('embedding', '1/gemini-embedding-001/768');
                    INSERT INTO documents VALUES ('abc', 'legacy.txt', 'old-digest', 1, '2025-01-01T00:00:00Z');
                """)
                old.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)", ("abc", 1, "Document body", BOOK, bytes(DIMENSIONS * 4)))
                old.commit()
            finally:
                old.close()
            store = Store(self.settings.database_url, self.settings.embedding_model)
            try:
                store.open()
                self.assertEqual(copy_index(source, store), 1)
                with self.assertRaisesRegex(ValueError, "destination contains documents"):
                    copy_index(source, store)
            finally:
                store.close()
        self.assertEqual(self.client.get("/api/documents").json()["documents"][0]["filename"], "legacy.txt")
        self.assertEqual(self.fake.calls, [])

    def test_declines_on_invalid_quote(self):
        self.upload()
        self.fake.fake_quote = True
        result = self.ask("How many books?").json()
        self.assertEqual(result["statements"], [])
        self.assertIn("couldn’t find enough evidence", result["message"])

    def test_no_evidence_skips_generation(self):
        self.assertEqual(self.ask("How many books?").json()["statements"], [])
        self.assertEqual(self.fake.calls, [])
        self.upload()
        self.assertEqual(self.ask("What is the weather?").json()["statements"], [])
        self.assertFalse(any(call.endswith(":generateContent") for call in self.fake.calls))

    def test_provider_failure_leaves_no_partial_document(self):
        self.fake.quota_error = True
        self.assertEqual(self.upload().status_code, 429)
        self.assertEqual(self.client.get("/api/documents").json()["documents"], [])
        self.fake.quota_error = False
        self.assertEqual(self.upload().status_code, 201)

    def test_rejects_unsupported_blank_and_oversize_files(self):
        self.assertEqual(self.upload("policy.exe").status_code, 422)
        self.assertEqual(self.upload(text=" \n ").status_code, 422)
        self.assertEqual(self.upload(text=b"x" * (MAX_UPLOAD_BYTES + 1)).status_code, 413)
        self.assertEqual(self.upload(text=b"x" * (MAX_UPLOAD_BYTES + 70_000)).status_code, 413)
        self.assertEqual(self.fake.calls, [])

    def test_request_validation_and_protection(self):
        self.assertEqual(self.ask("   ").status_code, 422)
        self.assertEqual(self.ask("a" * 1001).status_code, 422)
        self.assertEqual(self.client.get("/", headers={"Host": "unknown.example"}).status_code, 400)
        self.assertEqual(self.client.post("/api/ask", headers={"X-DocDesk-Request": ""}).status_code, 403)
        self.assertIn("frame-ancestors 'none'", self.client.get("/").headers["content-security-policy"])

    def test_shared_mode_requires_credentials(self):
        protected = replace(self.settings, password="fake-test-password")
        with self.session(protected) as client:
            self.assertEqual(client.get("/api/documents").status_code, 401)
            self.assertEqual(client.get("/", auth=("docdesk", protected.password)).status_code, 200)


class ExtractionTests(unittest.TestCase):
    def test_docx_body_and_table(self):
        document = Document()
        document.add_paragraph(BOOK)
        table = document.add_table(rows=1, cols=1)
        table.cell(0, 0).text = HOURS
        data = BytesIO()
        document.save(data)
        text = make_chunks("policy.docx", data.getvalue())[0].text
        self.assertIn(BOOK, text)
        self.assertIn(HOURS, text)

    def test_pdf_keeps_page_number(self):
        pdf = PdfWriter()
        pdf.add_blank_page(width=612, height=792)
        page = pdf.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): DictionaryObject({
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }),
            }),
        })
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 50 700 Td ({BOOK}) Tj ET".encode())
        page.replace_contents(content)
        buffer = BytesIO()
        pdf.write(buffer)
        chunk = make_chunks("policy.pdf", buffer.getvalue())[0]
        self.assertEqual((chunk.location, chunk.text), ("Page 2", BOOK))

    def test_chunking_covers_words_and_keeps_overlap(self):
        text = " ".join(f"word{number}" for number in range(350))
        chunks = make_chunks("long.md", text.encode())
        self.assertGreater(len(chunks), 1)
        self.assertTrue(set(chunks[0].text.split()) & set(chunks[1].text.split()))
        self.assertEqual(set(text.split()), set(" ".join(chunk.text for chunk in chunks).split()))


if __name__ == "__main__":
    unittest.main()
