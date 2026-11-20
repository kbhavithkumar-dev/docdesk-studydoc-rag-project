"""Connect indexing, vector search, and answer generation."""

from hashlib import sha256

from .documents import clean_filename, make_chunks
from .gemini import Gemini
from .store import Store

TOP_K = 5
NO_EVIDENCE = "I couldn’t find enough evidence in the selected documents to answer that question."


def index_document(filename: str, data: bytes, store: Store, gemini: Gemini) -> tuple[dict, bool]:
    filename = clean_filename(filename)
    # Hash the bytes so uploading the same file again can reuse the existing index.
    digest = sha256(data).hexdigest()
    existing = store.find_duplicate(digest)
    if existing:
        return existing, True
    chunks = make_chunks(filename, data)
    vectors = gemini.embed([chunk.text for chunk in chunks])
    # Write only after all embeddings succeed. A quota error leaves no partial document.
    return store.add_document(filename, digest, chunks, vectors)


def ask(question: str, document_id: str | None, store: Store, gemini: Gemini) -> dict:
    if not store.has_documents(document_id):
        return {"statements": [], "message": NO_EVIDENCE}
    query = gemini.embed([question], is_query=True)[0]
    matches = store.search(
        query, document_id=document_id, threshold=gemini.settings.min_similarity, top_k=TOP_K,
    )
    if not matches:
        return {"statements": [], "message": NO_EVIDENCE}

    evidence = [{"source_id": i, "text": match.text} for i, match in enumerate(matches, 1)]
    draft = gemini.answer(question, evidence)

    statements = []
    for item in draft.statements:
        if item.source_id > len(matches):
            return {"statements": [], "message": NO_EVIDENCE}
        match = matches[item.source_id - 1]
        quote = " ".join(item.quote.split())
        # Verify the cited words actually came from the retrieved file.
        if not any(char.isalnum() for char in quote) or quote not in match.text or not item.text.strip():
            return {"statements": [], "message": NO_EVIDENCE}
        statements.append({
            "text": item.text.strip(),
            "filename": match.filename,
            "location": match.location,
            "chunk_number": match.chunk_number,
            "quote": quote,
            "excerpt": match.text,
        })
    return {"statements": statements, "message": "" if statements else NO_EVIDENCE}
