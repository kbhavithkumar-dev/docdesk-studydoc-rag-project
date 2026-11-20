// Connect the form to FastAPI and turn the returned rows into a table.
const question = document.getElementById("question");
const form = document.getElementById("question-form");
const askButton = document.getElementById("ask-button");
const exampleList = document.getElementById("examples");
const statusMessage = document.getElementById("status");
const result = document.getElementById("result");

async function loadExamples() {
  const response = await fetch("/api/examples");
  if (!response.ok) throw new Error("Could not load the example questions.");
  const data = await response.json();
  document.getElementById("mode").hidden = !data.demo_mode;
  for (const example of data.questions) {
    const button = document.createElement("button");
    button.textContent = example;
    button.addEventListener("click", () => {
      question.value = example;
      question.focus();
    });
    exampleList.append(button);
  }
}

function paintResult(data) {
  document.getElementById("summary").textContent = data.summary;
  document.getElementById("sql").textContent = data.sql;
  const columns = document.getElementById("columns");
  const rows = document.getElementById("rows");
  columns.replaceChildren();
  rows.replaceChildren();

  for (const column of data.columns) {
    const heading = document.createElement("th");
    heading.scope = "col";
    heading.textContent = column.replaceAll("_", " ");
    columns.append(heading);
  }
  for (const values of data.rows) {
    const row = rows.insertRow();
    for (const value of values) {
      // textContent displays database values as text even if they contain HTML.
      row.insertCell().textContent = value ?? "—";
    }
  }
  result.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!question.value.trim()) return;

  askButton.disabled = true;
  result.hidden = true;
  statusMessage.className = "";
  statusMessage.textContent = "Running the sales query…";
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: question.value.trim()}),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Enter a question up to 500 characters.");
    }
    paintResult(data);
    statusMessage.textContent = "";
  } catch (error) {
    statusMessage.className = "error";
    statusMessage.textContent = error.message;
  } finally {
    askButton.disabled = false;
  }
});

loadExamples().catch(error => {
  statusMessage.className = "error";
  statusMessage.textContent = error.message;
});
