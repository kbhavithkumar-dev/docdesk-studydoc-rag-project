"""Copy a DocDesk v1 index into an empty v2 PostgreSQL database."""

from argparse import ArgumentParser
from contextlib import closing
from pathlib import Path
import sqlite3

from docdesk.settings import Settings
from docdesk.store import Store


def copy_index(source, store):
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as old:
        # A consistent snapshot keeps document and chunk rows together.
        old.execute("BEGIN")
        previous = old.execute("SELECT value FROM index_config WHERE name = 'embedding'").fetchone()
        with store.connect() as new:
            store.lock_writes(new)
            current = new.execute("SELECT value FROM index_config WHERE name = 'embedding'").fetchone()
            if not previous or previous[0] != current["value"]:
                raise ValueError("The old index uses a different embedding configuration.")
            if new.execute("SELECT 1 FROM documents LIMIT 1").fetchone():
                raise ValueError("The destination contains documents. Use an empty database.")
            with new.cursor() as cursor:
                documents = old.execute("SELECT id, filename, digest, chunk_count, added_at FROM documents")
                cursor.executemany("INSERT INTO documents VALUES (%s, %s, %s, %s, %s)", documents)
                chunks = old.execute("SELECT document_id, number, location, text, vector FROM chunks")
                cursor.executemany("INSERT INTO chunks VALUES (%s, %s, %s, %s, %s)", chunks)
            return new.execute("SELECT count(*) AS count FROM documents").fetchone()["count"]


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    store = Store(settings.database_url, settings.embedding_model)
    try:
        store.open()
        store.initialize()
        count = copy_index(args.source, store)
        print(f"Imported {count} documents.")
    finally:
        store.close()


if __name__ == "__main__":
    main()
