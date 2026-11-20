"use strict";

// Use browser APIs directly so this app needs no frontend build step.
const byId = (id) => document.getElementById(id);
const uploadForm = byId("upload-form");
const askForm = byId("ask-form");
const file = byId("file");
const scope = byId("scope");
const question = byId("question");
let documents = [];
let busy = false;

function node(tag, text, className) {
  const element = document.createElement(tag);
  // Text from documents and Gemini is displayed as text, never executed as HTML.
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {"X-DocDesk-Request": "1", ...options.headers},
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : "The request failed. Review your input and try again.");
  }
  return data;
}

function feedback(id, text, error = false) {
  const element = byId(id);
  element.textContent = text;
  element.dataset.error = String(error);
}

function updateControls() {
  file.disabled = busy;
  scope.disabled = busy;
  byId("upload-button").disabled = busy || !file.files.length;
  byId("ask-button").disabled = busy || !documents.length || !question.value.trim();
  for (const button of byId("documents").querySelectorAll("button")) button.disabled = busy;
}

async function perform(statusId, pending, action) {
  if (busy) return;
  busy = true;
  updateControls();
  feedback(statusId, pending);
  try {
    await action();
  } catch (error) {
    feedback(statusId, error.message || "The request failed.", true);
  } finally {
    busy = false;
    updateControls();
  }
}

async function refreshDocuments() {
  const result = await request("/api/documents");
  documents = result.documents;
  const previousScope = scope.value;
  scope.replaceChildren(new Option("All documents", ""));
  const list = byId("documents");
  list.replaceChildren();
  for (const documentInfo of documents) {
    scope.add(new Option(documentInfo.filename, documentInfo.id));
    const item = node("li", "");
    const info = node("div", "", "file-info");
    const count = documentInfo.chunk_count;
    info.append(node("strong", documentInfo.filename), node("span", `${count} ${count === 1 ? "excerpt" : "excerpts"}`, "muted"));
    const remove = node("button", "Remove", "remove");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${documentInfo.filename}`);
    remove.addEventListener("click", () => {
      if (!window.confirm(`Remove "${documentInfo.filename}" from the library?`)) return;
      perform("library-status", "Removing document…", async () => {
        await request(`/api/documents/${documentInfo.id}`, {method: "DELETE"});
        byId("answer").hidden = true;
        await refreshDocuments();
        feedback("library-status", "Document removed.");
      });
    });
    item.append(info, remove);
    list.append(item);
  }
  if (documents.some((doc) => doc.id === previousScope)) scope.value = previousScope;
  byId("count").textContent = documents.length;
  byId("count").setAttribute("aria-label", `${documents.length} documents`);
  byId("library-empty").hidden = Boolean(documents.length);
  updateControls();
}

file.addEventListener("change", () => {
  byId("file-label").textContent = file.files[0]?.name ?? "Choose a document";
  feedback("library-status", "");
  updateControls();
});
question.addEventListener("input", updateControls);

uploadForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const selected = file.files[0];
  if (!selected || busy) return;
  if (selected.size > 8 * 1024 * 1024) {
    feedback("library-status", "Each file must be at most 8 MB.", true);
    return;
  }
  const form = new FormData();
  form.append("file", selected);
  perform("library-status", "Extracting text and creating embeddings…", async () => {
    const result = await request("/api/documents", {method: "POST", body: form});
    uploadForm.reset();
    byId("file-label").textContent = "Choose a document";
    await refreshDocuments();
    feedback("library-status", result.duplicate ? "This file is already indexed." : "Document indexed.");
  });
});

function showAnswer(result) {
  const answerText = byId("answer-text");
  const sources = byId("sources");
  answerText.replaceChildren();
  sources.replaceChildren();
  if (result.message) answerText.append(node("p", result.message));

  result.statements.forEach((statement, index) => {
    const number = index + 1;
    const paragraph = node("p", statement.text);
    const citation = node("a", `[${number}]`, "citation");
    const details = node("details", "", "source");
    details.id = `source-${number}`;
    citation.href = `#${details.id}`;
    citation.setAttribute("aria-label", `View source ${number}`);
    citation.addEventListener("click", (event) => {
      event.preventDefault();
      details.open = true;
      details.querySelector("summary").focus();
    });
    paragraph.append(citation);
    answerText.append(paragraph);

    const title = `[${number}] ${statement.filename} · ${statement.location} · Excerpt ${statement.chunk_number}`;
    details.append(node("summary", title), node("blockquote", statement.quote), node("p", statement.excerpt));
    sources.append(details);
  });
  byId("answer").hidden = false;
  byId("answer-title").focus();
}

askForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!question.value.trim() || !documents.length || busy) return;
  const body = JSON.stringify({question: question.value.trim(), document_id: scope.value || null});
  byId("answer").hidden = true;
  perform("ask-status", "Searching your documents for evidence…", async () => {
    const result = await request("/api/ask", {
      method: "POST", headers: {"Content-Type": "application/json"}, body,
    });
    showAnswer(result);
    feedback("ask-status", "");
  });
});

perform("library-status", "Loading documents…", async () => {
  await refreshDocuments();
  feedback("library-status", "");
});
