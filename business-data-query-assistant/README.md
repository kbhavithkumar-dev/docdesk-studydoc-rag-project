# Business Data Query Assistant

Ask sales questions in plain English and see the answer, the result table, and the PostgreSQL query that produced it. The app uses Python, FastAPI, PostgreSQL, and Gemini through LangChain. The sample shop data is fictional.

## Run in VS Code

Install Python 3.12 and Docker with Docker Compose. Open this folder in VS Code. Run these commands in its terminal.

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up -d --wait
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

**macOS or Linux**

```bash
cp .env.example .env
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
docker compose up -d --wait
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Choose **What is the total revenue?** and click **Run query**. The sample orders total **₹102,700**. Expand **View SQL** to see the executed statement. Demo mode supports all four example questions and runs real SQL against the sample database.

To turn on AI mode, create a [Gemini API key](https://aistudio.google.com/apikey), set `GEMINI_API_KEY` and `DEMO_MODE=false` in `.env`, and restart FastAPI. Try **Show total revenue for monitors in the West region.** The app sends the question and schema description to Gemini. PostgreSQL results stay in the app. Model access and usage charges depend on your Google project.

## Follow one question through the code

1. `static/app.js` sends the question to the FastAPI route in `main.py`.
2. `assistant.py` gives Gemini the available columns and asks for one SQL query. Demo mode looks up a fixed example query.
3. `sql_guard.py` uses PostgreSQL's parser to inspect the SQL. The app accepts a small reporting subset with filters, aggregates, sorting, and grouping over `reporting.sales`. It rejects writes and multi-statement input and caps returned rows.
4. `assistant.py` runs the checked query in a read-only transaction using `report_reader`. The database setup grants that account access to the reporting view.
5. FastAPI returns the exact values and a plain-language summary. The JavaScript displays the result table and SQL.

Both services bind to loopback in the setup above. This is a local learning project. The app has no login or per-user access controls. Use the demo data for interviews. Changing the database initialization script or credentials after Docker's first run does not reinitialize the existing data volume.

## Verify

Run `python -m unittest discover -s tests -v` with the virtual environment's Python. The SQL and route tests can run without PostgreSQL or Gemini. With Docker PostgreSQL running, set `RUN_DB_TESTS=1` to include the integration tests. In PowerShell use `$env:RUN_DB_TESTS = "1"`. On macOS or Linux use `RUN_DB_TESTS=1 .venv/bin/python -m unittest discover -s tests -v`.

Stop the web app with Ctrl+C. Run `docker compose down` to stop PostgreSQL and preserve the sample data volume.
