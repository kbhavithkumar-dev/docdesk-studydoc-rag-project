# DocDesk

Ask questions about your own documents and open the cited excerpts to see where each answer came from. DocDesk combines a responsive web interface, FastAPI, PostgreSQL, and Gemini.

## Start in VS Code

1. Install Docker with Docker Compose. Clone this repository and open its folder in VS Code.
2. Copy `.env.example` to `.env`. Use `Copy-Item .env.example .env` in PowerShell or `cp .env.example .env` in macOS or Linux.
3. Create a [Gemini API key in Google AI Studio](https://aistudio.google.com/apikey) and set `GEMINI_API_KEY` in `.env`. Leave the other values as provided for a loopback-only demo.
4. Run `docker compose up --build -d --wait` from the repository root.
5. Open [DocDesk on port 8000](http://localhost:8000). PostgreSQL is available locally on port 5434. Set `WEB_PORT` or `DOCDESK_DB_PORT` if a port is already in use.

Upload `examples/library-policy.txt`. Ask **How many books can a student borrow, and for how long?** The source says **four books** and **14 calendar days**. Click the citation to see the exact passage. Ask **How much are overdue fines?** to exercise the insufficient-evidence response. Restart the app and confirm the document is still listed.

Use `docker compose logs app` to see server logs and `docker compose down` to stop both services while keeping the database volume.

## Run Python directly

To edit and debug the API in VS Code, start only PostgreSQL with `docker compose up -d --wait db`. Install Python 3.12 and run the commands for your OS.

**Windows PowerShell**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn docdesk.main:create_app --factory --host 127.0.0.1 --port 8000 --limit-concurrency 16
```

**macOS or Linux**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn docdesk.main:create_app --factory --host 127.0.0.1 --port 8000 --limit-concurrency 16
```

## Where to read the code

| File | What it does |
| --- | --- |
| `docdesk/index.html`, `docdesk/static/` | File picker, question form, document list, and clickable citations. |
| `docdesk/main.py` | API routes, request limits, readiness checks, and password protection. |
| `docdesk/documents.py` | Reads PDFs, DOCX, TXT, and Markdown and splits text into overlapping chunks. |
| `docdesk/gemini.py` | Requests embeddings and structured answers from Gemini. |
| `docdesk/store.py` | Uses PostgreSQL connections and transactions to manage the document index. |
| `docdesk/retrieval.py` | Selects relevant excerpts and checks each citation against the original text. |

Read [Concepts and workflow](docs/WORKFLOW.md) for diagrams, a worked example, and the limits of the design.

## Database and hosting

The default database container provisions a dedicated `docdesk` role with its own schema. For hosted PostgreSQL, set `DATABASE_URL` to a DSN for a dedicated login that owns an empty schema and has that schema first in its search path. Enable TLS and follow your provider’s certificate setup. Keep database credentials and the Gemini API key on the server.

For shared hosting, put the app behind HTTPS with rate limits and explicit allowed hostnames. Configure `APP_USERNAME`, a unique `APP_PASSWORD` of at least 16 ASCII characters, and `ALLOWED_HOSTS` including the public hostname and `localhost,127.0.0.1` for local probes. Set a proxy upload body limit of 9 MiB and allow up to 180 seconds for indexing. Use a private database, rotate the local demo passwords before hosting, and make regular PostgreSQL backups. `/healthz` checks the process and `/readyz` checks the index database.

### Upgrading from the SQLite version

Stop the old app and back up its `data` directory. With the new PostgreSQL instance running, the **same embedding model** configured, and no documents in the destination, run this from the repository root using the virtual environment and the host database port.

```bash
python -m scripts.import_sqlite /path/to/docdesk.sqlite3
```

The importer copies documents and chunks atomically without calling Gemini. Open the web app and verify the document list before retiring the old index. Changing the embedding model requires a fresh index and re-uploading the source files.

## Tests and limitations

Run `python -m unittest discover -s tests -v` with the virtual environment active. To include API and persistence tests, set `DOCDESK_TEST_DATABASE_URL` to a **disposable** PostgreSQL database whose login can create test schemas. These tests use controlled Gemini responses, so they require no API key. To check live Gemini, complete the sample-file walkthrough above.

- Supports PDF with selectable text, DOCX main body and tables, and UTF-8 TXT or Markdown. Scans require OCR before upload.
- Maximum 8 MiB per file, 150 pages per PDF, 250 documents, and 5,000 indexed excerpts. Exact similarity search is intended for this small scale.
- Citations prove that a quote exists in an excerpt. Review the source to confirm that it supports the answer.
- Gemini receives document chunks, questions, and retrieved excerpts. Upload only documents you are allowed to send to the provider.
- The library is shared by authenticated users in one deployment. Anonymous uploads, per-user access, and OCR are outside this design.

The indexing and retrieval pattern follows the [original Gemini RAG tutorial](https://laxmi916.github.io/nlp/RAG.html).
