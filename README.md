# DocDesk · Document Q&A with Gemini

Upload a document, ask a question, and open the citation to see the exact passage behind the answer. DocDesk is a deployment-oriented starter for one user or a small trusted team. It uses real Gemini embeddings and text generation at runtime.

This project follows the embed → retrieve → augment → generate flow in the [RAG beginner tutorial](https://laxmi916.github.io/nlp/RAG.html). It keeps the tutorial’s Gemini embedding model and adds file parsing, a browser interface, and persistent storage. It uses SQLite for the index and configures Gemini 2.5 Flash-Lite for generation.

## 1. Run it in VS Code

Extract the ZIP. Open the extracted `docdesk` folder in VS Code. Open its terminal at the folder containing `requirements.txt`. Install Python 3.12 and create a virtual environment.

**Windows PowerShell**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

**macOS or Linux**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Create a [Gemini API key in Google AI Studio](https://aistudio.google.com/apikey). In VS Code, open `.env` and paste your key after `GEMINI_API_KEY=`. The server sends extracted text, search questions, and selected passages to Google Gemini. Use documents you are permitted to send to that service.

Start the server with the command for your OS.

```powershell
.\.venv\Scripts\python.exe -m uvicorn docdesk.main:create_app --factory --host 127.0.0.1 --port 8000 --limit-concurrency 16
```

```bash
.venv/bin/python -m uvicorn docdesk.main:create_app --factory --host 127.0.0.1 --port 8000 --limit-concurrency 16
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Keep the terminal running while you use the app. Press Ctrl+C to stop the server.

## 2. Try one complete example

1. Upload `examples/library-policy.txt` and wait for **Document indexed**.
2. Ask **How many books can a student borrow, and for how long?**. The sample says **four books** and **14 calendar days**. Click the citation numbers to see the passages.
3. Ask **How much are overdue fines?**. The sample provides no answer, so the expected result is an insufficient-evidence message.
4. Restart the server with the same `DATA_DIR`. The document should still appear in the library.

Supported uploads are text-based PDF, DOCX, UTF-8 TXT, and UTF-8 Markdown. DOCX includes the main body and table text. Scanned PDFs require OCR before upload. The app accepts one file per request, up to 8 MB and 150 PDF pages. The library limits are 250 documents and 5,000 excerpts.

## 3. Read the code in this order

| File | What to look for |
| --- | --- |
| `docdesk/index.html` and `docdesk/static/` | The two forms, browser requests, and expandable citations. |
| `docdesk/main.py` | The HTTP routes that receive a file or question and return JSON. |
| `docdesk/documents.py` | How uploaded text is extracted and split into overlapping chunks. |
| `docdesk/gemini.py` | How the server makes an embedding batch and asks Gemini for a structured answer. |
| `docdesk/store.py` | How SQLite saves chunks and their vectors, keeps an upload atomic, and ranks search results. |
| `docdesk/retrieval.py` | How retrieved text becomes evidence and how each quote is checked. |
| `docdesk/settings.py` | Defaults, runtime limits, and settings read from `.env`. |
| `tests/test_app.py` | Examples of testing the complete pipeline with controlled provider responses. |

### What happens when you upload

The server extracts readable text and splits it into excerpts of up to 800 characters. Adjacent excerpts may overlap so a sentence near a boundary still has context. Gemini converts each excerpt into a 768-number vector. SQLite saves the original excerpt, its location, and the vector together. The original uploaded binary is discarded. The text remains until the document is removed. Re-uploading identical bytes reuses the existing entry.

### What happens when you ask

The same embedding model converts your question into a vector. The server compares it with the saved excerpt vectors using cosine similarity. It sends up to five qualifying passages to Gemini, which writes short statements and chooses a supporting quote for each. The server verifies that the quoted words exist in the cited excerpt and attaches the filename and page or excerpt number. Checking that a quote exists helps prevent fabricated citations. It cannot prove that the quote supports every interpretation in the answer.

## 4. Verify the project

Run the tests from the project folder using the virtual environment’s Python.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The suite uses a simulated Gemini HTTP service. It covers PDF and DOCX extraction, upload, persistence, duplicate handling, source quotes, scoped search, invalid files, credential protection, and failed indexing. The sample in Step 2 is the live test with your Gemini key. Provider access and quotas depend on your Google project.

## 5. Deployment

Run the same app in Docker with a persistent named volume. Pass `DATA_DIR=/data` so the container writes to that volume.

```bash
docker build -t docdesk .
docker run --rm --name docdesk --env-file .env -e DATA_DIR=/data -p 127.0.0.1:8000:8000 -v docdesk-data:/data docdesk
```

The default bind address is local. For a shared server, keep the container port private behind an HTTPS reverse proxy, forward the original `Host` header, set `ALLOWED_HOSTS` to the deployment hostname, and configure `APP_USERNAME` plus an ASCII `APP_PASSWORD` of at least 16 characters. Configure the proxy to rate-limit requests, cap upload bodies at 9 MiB, and allow up to 180 seconds for indexing. The app uses one shared library and one credential pair. It is designed for trusted users. An internet-facing service that accepts anonymous files needs isolated parsing workers, per-user access controls, monitoring, and separate deployment testing.

`GET /healthz` checks server liveness. Keep the data volume across updates. For an offline backup, stop the app before copying the database directory. If you change the embedding model, point `DATA_DIR` at a new empty directory and re-upload. The configured embedding model must support the Gemini retrieval task types used by this app. The chat model can change without re-indexing.

For a managed proxy, standard `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` environment variables are supported by the server’s HTTP client. Set any required trusted CA with `SSL_CERT_FILE`. Keep proxy credentials and the Gemini key out of source control.

### Troubleshooting

| Symptom | Check |
| --- | --- |
| Upload says the API key is missing | Check that `.env` is beside `requirements.txt`, then restart the app. |
| Gemini rejected the request | Check API access for the key and the configured model names in Google AI Studio. |
| Gemini returned a quota error | Check your project limits. Large files make several embedding batches. |
| No readable text found | If you cannot select PDF text, run OCR and re-upload. |
| Evidence is missing for a known answer | Check the selected document and try a more specific question. `MIN_SIMILARITY` controls which excerpts qualify. Evaluate changes with questions you can verify. |

[Gemini embedding API](https://ai.google.dev/api/embeddings) · [Gemini generation API](https://ai.google.dev/api/generate-content)
