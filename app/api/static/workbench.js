"use strict";

const form = document.getElementById("case-form");
const assessment = document.getElementById("assessment");
const documents = document.getElementById("documents");
const signals = document.getElementById("signals");
const errorBox = document.getElementById("form-error");
const button = document.getElementById("analyze-button");
const loading = document.getElementById("loading");
const loadingCopy = document.getElementById("loading-copy");
const results = document.getElementById("results");
const metadataRoot = document.getElementById("document-metadata");

function fileSummary(input) {
  if (!input.files.length) return input === documents ? "No clinical documents selected" : `No ${input === assessment ? "assessment" : "structured signal file"} selected`;
  if (input.files.length === 1) return `${input.files[0].name} (${formatBytes(input.files[0].size)})`;
  const total = Array.from(input.files).reduce((sum, file) => sum + file.size, 0);
  return `${input.files.length} files (${formatBytes(total)} total)`;
}

function formatBytes(value) {
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

[assessment, documents, signals].forEach((input) => {
  input.addEventListener("change", () => {
    document.querySelector(`[data-file-state="${input.id}"]`).textContent = fileSummary(input);
  });
});

function renderDocumentMetadata() {
  const previous = new Map(
    Array.from(metadataRoot.querySelectorAll(".metadata-row")).map((row) => [
      row.dataset.filename,
      { date: row.querySelector("input").value, type: row.querySelector("select").value },
    ]),
  );
  metadataRoot.replaceChildren();
  Array.from(documents.files).forEach((file) => {
    const row = document.createElement("div");
    row.className = "metadata-row";
    row.dataset.filename = file.name;
    const name = document.createElement("strong");
    name.textContent = file.name;
    name.title = file.name;
    const dateLabel = document.createElement("label");
    dateLabel.textContent = "Document date";
    const dateInput = document.createElement("input");
    dateInput.type = "date";
    dateInput.required = true;
    dateInput.setAttribute("aria-label", `Document date for ${file.name}`);
    dateInput.value = previous.get(file.name)?.date || "";
    dateLabel.append(dateInput);
    const typeLabel = document.createElement("label");
    typeLabel.textContent = "Document type";
    const typeSelect = document.createElement("select");
    typeSelect.setAttribute("aria-label", `Document type for ${file.name}`);
    [
      ["clinical_document", "Clinical document"],
      ["dietary", "Dietary"],
      ["nursing_note", "Nursing note"],
      ["physician_note", "Physician note"],
      ["therapy_eval", "Therapy evaluation"],
      ["mar", "Medication administration"],
    ].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      typeSelect.append(option);
    });
    typeSelect.value = previous.get(file.name)?.type || "clinical_document";
    typeLabel.append(typeSelect);
    row.append(name, dateLabel, typeLabel);
    metadataRoot.append(row);
  });
}

documents.addEventListener("change", renderDocumentMetadata);

function summaryItem(label, value) {
  const root = document.createElement("div");
  root.className = "summary-item";
  const caption = document.createElement("span");
  caption.textContent = label;
  const strong = document.createElement("strong");
  strong.textContent = value;
  root.append(caption, strong);
  return root;
}

function citationNode(citation) {
  const root = document.createElement("div");
  root.className = "citation";
  const quote = document.createElement("blockquote");
  quote.textContent = citation.quote;
  const source = document.createElement("small");
  const page = citation.page ? `, page ${citation.page}` : "";
  const confidence = citation.confidence == null ? "" : `, OCR ${(citation.confidence * 100).toFixed(0)}%`;
  const bbox = citation.bbox == null ? "" : `, bbox [${citation.bbox.map((value) => Number(value).toFixed(0)).join(", ")}]`;
  const offsets = citation.text_start == null ? "" : `, text ${citation.text_start}–${citation.text_end}`;
  source.textContent = `${citation.document_name || citation.document_id}${page}${confidence}${bbox}${offsets}`;
  root.append(quote, source);
  return root;
}

function findingNode(finding) {
  const root = document.createElement("article");
  root.className = "finding";
  const meta = document.createElement("div");
  meta.className = "finding-meta";
  const labels = {
    structured_supported: "Structured supported",
    text_review_candidate: "Text review candidate",
    review_flag: "Review flag",
  };
  const direction = labels[finding.support_tier] || "Review required";
  const directionNode = document.createElement("strong");
  directionNode.textContent = direction;
  meta.append(
    directionNode,
    document.createElement("br"),
    document.createTextNode(finding.mds_item),
    document.createElement("br"),
    document.createTextNode(finding.stream.replaceAll("_", " ")),
  );

  const body = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = finding.title;
  const rationale = document.createElement("p");
  rationale.textContent = finding.rationale;
  const change = document.createElement("div");
  change.className = "change";
  const current = document.createElement("div");
  current.innerHTML = `<span>As coded</span><strong></strong>`;
  current.querySelector("strong").textContent = finding.current_value || "Blank";
  const proposed = document.createElement("div");
  proposed.innerHTML = `<span>Proposed review</span><strong></strong>`;
  proposed.querySelector("strong").textContent = finding.suggested_value || "Review required";
  change.append(current, proposed);

  body.append(title, rationale, change);
  const delta = finding.counterfactual.potential_per_day_delta;
  if (delta != null) {
    const money = document.createElement("p");
    const counted = finding.counterfactual.counted_per_day_delta;
    money.textContent = counted == null
      ? `$${delta}/day potential Scenario-A movement; not counted.`
      : `$${delta}/day potential and counted Scenario-A movement.`;
    body.append(money);
  }
  if (finding.counterfactual.as_supported) {
    const hipps = document.createElement("p");
    hipps.textContent = `${finding.counterfactual.as_coded.hipps || "Ungrouped"} → ${finding.counterfactual.as_supported.hipps || "Ungrouped"} HIPPS`;
    body.append(hipps);
  }
  finding.evidence.filter((item) => item.verified).forEach((item) => body.append(citationNode(item)));
  root.append(meta, body);
  return root;
}

function render(data) {
  const potential = data.summary.potential_scenario_a_movement;
  const counted = data.summary.counted_movement;
  const summary = document.getElementById("result-summary");
  summary.replaceChildren(
    summaryItem("As-coded HIPPS", data.assessment.as_coded_hipps || "Blocked"),
    summaryItem("Baseline", data.assessment.baseline.status.replaceAll("_", " ")),
    summaryItem("Structured supported", String(data.summary.structured_supported)),
    summaryItem("Text review candidates", String(data.summary.text_review_candidates)),
    summaryItem("Potential Scenario-A movement", potential.per_day_delta == null ? "Unavailable" : `$${potential.per_day_delta}/day`),
    summaryItem("Counted movement", counted?.per_day_delta == null ? "Not counted" : `$${counted.per_day_delta}/day`),
  );

  const baselineWarning = document.getElementById("baseline-warning");
  const baselineStatus = data.assessment.baseline.status;
  baselineWarning.hidden = baselineStatus === "matched";
  baselineWarning.textContent = {
    mismatch: "Baseline mismatch: the supplied reconciled HIPPS differs from the as-coded CMS grouper result. Clinical analysis continues, but no movement is counted.",
    unreconciled: "Unreconciled baseline: clinical analysis continues, but no movement is counted.",
    missing: "Missing baseline: potential scenarios are shown, but no movement is counted.",
    not_billed: "Not billed: potential scenarios are shown, but no movement is counted.",
  }[baselineStatus] || "";

  const list = document.getElementById("finding-list");
  list.replaceChildren();
  if (!data.findings.length) {
    const empty = document.createElement("div");
    empty.className = "empty-result";
    const title = document.createElement("h3");
    title.textContent = data.assessment.groupable ? "No qualifying candidate found in the current rule scope" : "Assessment could not be grouped";
    const copy = document.createElement("p");
    copy.textContent = data.assessment.groupable
      ? "The uploaded record did not produce a qualifying NTA, Section I, Section K, or Section GG change. Review the decision log for excluded or conflicting evidence."
      : "The primary diagnosis or assessment content produced no billable HIPPS. Correct the assessment before recapture analysis.";
    empty.append(title, copy);
    list.append(empty);
  } else {
    data.findings.forEach((finding) => list.append(findingNode(finding)));
  }

  const detail = document.getElementById("run-detail");
  const pre = document.createElement("pre");
  pre.tabIndex = 0;
  pre.setAttribute("aria-label", "Run provenance JSON");
  pre.textContent = JSON.stringify({ run_id: data.run_id, assessment: data.assessment, inputs: data.inputs, ocr: data.ocr, decisions: data.decisions, scope: data.scope }, null, 2);
  detail.replaceChildren(pre);
  results.hidden = false;
  results.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  results.hidden = true;
  if (!assessment.files.length) {
    errorBox.textContent = "Select an MDS XML or JSON assessment.";
    errorBox.hidden = false;
    assessment.focus();
    return;
  }
  if (!documents.files.length && !signals.files.length) {
    errorBox.textContent = "Select at least one clinical document or structured signal file.";
    errorBox.hidden = false;
    documents.focus();
    return;
  }

  const documentMetadata = Array.from(metadataRoot.querySelectorAll(".metadata-row")).map((row) => ({
    filename: row.dataset.filename,
    document_date: row.querySelector("input").value,
    document_type: row.querySelector("select").value,
  }));
  if (documentMetadata.some((item) => !item.document_date)) {
    errorBox.textContent = "Enter a trusted document date for every clinical document.";
    errorBox.hidden = false;
    metadataRoot.querySelector("input:invalid")?.focus();
    return;
  }

  const payload = new FormData();
  payload.append("assessment", assessment.files[0]);
  Array.from(documents.files).forEach((file) => payload.append("documents", file));
  if (documentMetadata.length) payload.append("document_metadata", JSON.stringify(documentMetadata));
  if (signals.files.length) payload.append("signals", signals.files[0]);

  button.disabled = true;
  button.textContent = "Analyzing case";
  loading.hidden = false;
  loadingCopy.textContent = "OCR and CMS grouping may take up to three minutes.";
  try {
    const response = await fetch("/api/analyze", { method: "POST", body: payload });
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) throw new Error(data.error?.message || `Analysis failed (${response.status})`);
    render(data);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  } finally {
    loading.hidden = true;
    button.disabled = false;
    button.textContent = "Analyze uploaded case";
  }
});

document.getElementById("new-case").addEventListener("click", () => {
  results.hidden = true;
  form.reset();
  [assessment, documents, signals].forEach((input) => {
    document.querySelector(`[data-file-state="${input.id}"]`).textContent = fileSummary(input);
  });
  metadataRoot.replaceChildren();
  form.scrollIntoView({ behavior: "smooth", block: "start" });
});
