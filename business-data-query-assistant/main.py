"""FastAPI connects the web form to the query assistant."""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints
import psycopg
from typing import Annotated

from assistant import EXAMPLES, QuestionError, demo_mode, display_value, explain, make_sql, run_query
from sql_guard import UnsafeSQL, validate_sql

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
app = FastAPI(title="Business Data Query Assistant")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class Question(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


@app.get("/")
def home():
    return FileResponse(ROOT / "index.html")


@app.get("/api/examples")
def examples():
    return {"questions": list(EXAMPLES), "demo_mode": demo_mode()}


@app.post("/api/ask")
def ask(body: Question):
    try:
        generated_sql = make_sql(body.question)
    except QuestionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        # Provider errors can include sensitive configuration. Return a simple message.
        raise HTTPException(502, "Gemini could not generate SQL. Check the API key, model, and quota.") from exc

    try:
        sql = validate_sql(generated_sql)
    except UnsafeSQL as exc:
        raise HTTPException(422, str(exc)) from exc

    try:
        columns, rows, truncated = run_query(sql)
    except psycopg.errors.QueryCanceled as exc:
        raise HTTPException(408, "The query took too long. Try adding a date or product filter.") from exc
    except psycopg.OperationalError as exc:
        raise HTTPException(503, "PostgreSQL is unavailable. Check that the database is running.") from exc
    except psycopg.Error as exc:
        raise HTTPException(422, "PostgreSQL could not run this query. Try a simpler sales question.") from exc

    values = [[display_value(value) for value in row] for row in rows]
    return {"sql": sql, "columns": columns, "rows": values, "summary": explain(columns, values, truncated)}
