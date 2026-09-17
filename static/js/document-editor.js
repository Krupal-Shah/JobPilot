const documentModal = document.getElementById("documentModal");
const documentStatus = document.getElementById("documentStatus");
const documentEntries = document.getElementById("documentEntries");
const resumeWorkspace = document.getElementById("resumeWorkspace");
const textWorkspace = document.getElementById("textWorkspace");
const coverLetterPdf = document.getElementById("coverLetterPdf");
let documentSession = null;
let documentPdfBlobUrl = null;
let documentRequestNumber = 0;
let livePreviewTimer = null;
let livePreviewController = null;
let livePreviewNumber = 0;

function cancelLivePreview() {
  clearTimeout(livePreviewTimer);
  livePreviewTimer = null;
  livePreviewController?.abort();
  livePreviewController = null;
  ++livePreviewNumber;
}

function scheduleLivePreview() {
  if (documentSession?.kind !== "resume") return;
  clearTimeout(livePreviewTimer);
  livePreviewController?.abort();
  const previewNumber = ++livePreviewNumber;
  const session = documentSession;
  livePreviewTimer = setTimeout(async () => {
    livePreviewController = new AbortController();
    try {
      const response = await fetch(`${documentPath()}/preview`, {
        method: "POST",
        credentials: "same-origin",
        signal: livePreviewController.signal,
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": documentModal.dataset.csrf,
        },
        body: JSON.stringify({
          state: session.data.state,
          version: session.data.version,
        }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(
          result.error || "The PDF preview could not be updated.",
        );
      }
      const pdf = await response.blob();
      if (session !== documentSession || previewNumber !== livePreviewNumber)
        return;
      showDocumentPdf(pdf);
      documentMessage("Live preview updated. Save to keep these edits.");
    } catch (error) {
      if (
        error.name !== "AbortError" &&
        session === documentSession &&
        previewNumber === livePreviewNumber
      )
        documentMessage(error.message, true);
    }
  }, 500);
}

function scheduleCoverLetterPreview() {
  if (documentSession?.kind !== "cover_letter") return;
  clearTimeout(livePreviewTimer);
  livePreviewController?.abort();
  const previewNumber = ++livePreviewNumber;
  const session = documentSession;
  livePreviewTimer = setTimeout(async () => {
    livePreviewController = new AbortController();
    try {
      const response = await fetch(`${documentPath()}/preview`, {
        method: "POST",
        credentials: "same-origin",
        signal: livePreviewController.signal,
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": documentModal.dataset.csrf,
        },
        body: JSON.stringify({
          text: document.getElementById("documentText").value,
        }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(
          result.error || "The cover letter preview could not be updated.",
        );
      }
      const pdf = await response.blob();
      if (session !== documentSession || previewNumber !== livePreviewNumber)
        return;
      showDocumentPdf(pdf, "coverLetterPdf");
      documentMessage("Live preview updated. Save to keep this cover letter.");
    } catch (error) {
      if (
        error.name !== "AbortError" &&
        session === documentSession &&
        previewNumber === livePreviewNumber
      )
        documentMessage(error.message, true);
    }
  }, 500);
}

function documentMessage(message, error = false) {
  documentStatus.textContent = message;
  documentStatus.className = error ? "bad" : "";
  documentStatus.setAttribute("role", error ? "alert" : "status");
}

async function documentRequest(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": documentModal.dataset.csrf,
      ...(options.headers || {}),
    },
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(result.error || "The request failed. Please retry.");
  return result;
}

function documentPath() {
  return `/api/applications/${documentSession.application.id}/documents/${documentSession.kind.replace("_", "-")}`;
}

async function openDocumentEditor(application, kind) {
  cancelLivePreview();
  const requestNumber = ++documentRequestNumber;
  documentSession = { application, kind };
  documentModal._returnFocus = document.activeElement;
  documentModal.hidden = false;
  document.getElementById("documentTitle").textContent = {
    resume: "Tailor resume",
    cover_letter: "Cover letter",
    cold_email: "Cold email",
  }[kind];
  document.getElementById("documentJob").textContent =
    `${application.title} · ${application.company}`;
  resumeWorkspace.hidden = kind !== "resume";
  textWorkspace.hidden = kind === "resume";
  documentMessage("Loading draft…");
  document.getElementById("documentClose").focus();
  try {
    const data = await documentRequest(documentPath());
    if (requestNumber !== documentRequestNumber) return;
    documentSession.data = data;
    if (kind === "resume") {
      renderResumeEntries();
      renderMatch(data.score);
      document.getElementById("documentSuggestions").replaceChildren();
      const previewReady = await refreshDocumentPdf(data.pdf_url);
      if (previewReady)
        documentMessage(
          data.selection_method === "AI"
            ? "AI ranked the blocks. Review the selection and save any edits."
            : "Blocks were selected by keyword overlap. Review the selection and save any edits.",
        );
    } else {
      document.getElementById("documentText").value = data.text;
      updateDocumentWordCount();
      document.getElementById("documentTextLabel").textContent =
        kind === "cover_letter"
          ? "Cover letter draft"
          : "LinkedIn message draft";
      document.getElementById("documentSave").hidden = kind !== "cover_letter";
      document.getElementById("documentCopy").hidden = kind !== "cold_email";
      document.getElementById("textSuggestions").replaceChildren();
      if (kind === "cover_letter") scheduleCoverLetterPreview();
      documentMessage(
        kind === "cover_letter"
          ? data.saved
            ? "Saved cover letter loaded."
            : "Starter template loaded. Save when ready."
          : "This message stays in this editor until you close it; copy it before leaving.",
      );
    }
  } catch (error) {
    if (requestNumber === documentRequestNumber)
      documentMessage(error.message, true);
  }
}

function updateDocumentWordCount() {
  const kind = documentSession?.kind;
  const target = document.getElementById("documentWordCount");
  if (kind === "resume") {
    target.textContent = "";
    return;
  }
  const count = (
    document
      .getElementById("documentText")
      .value.match(/\b[\p{L}\p{N}]+(?:[-'][\p{L}\p{N}]+)*\b/gu) || []
  ).length;
  const limit = kind === "cover_letter" ? 350 : 100;
  target.textContent = `${count} of ${limit} words${count > limit ? " — shorten before saving or sending" : ""}`;
  target.classList.toggle("bad", count > limit);
}

document.getElementById("documentText").addEventListener("input", () => {
  updateDocumentWordCount();
  scheduleCoverLetterPreview();
});

function closeDocumentEditor() {
  cancelLivePreview();
  ++documentRequestNumber;
  documentModal.hidden = true;
  if (documentPdfBlobUrl) URL.revokeObjectURL(documentPdfBlobUrl);
  documentPdfBlobUrl = null;
  documentSession = null;
  documentModal._returnFocus?.focus();
}

document
  .getElementById("documentClose")
  .addEventListener("click", closeDocumentEditor);
documentModal.addEventListener("click", (event) => {
  if (event.target === documentModal) closeDocumentEditor();
});
document.addEventListener("keydown", (event) => {
  if (documentModal.hidden) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeDocumentEditor();
    return;
  }
  if (event.key !== "Tab") return;
  const controls = [
    ...documentModal.querySelectorAll("button, input, textarea, iframe"),
  ].filter(
    (element) =>
      !element.hidden && !element.disabled && element.getClientRects().length,
  );
  const first = controls[0],
    last = controls[controls.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

function renderMatch(score) {
  const node = document.getElementById("documentMatch");
  node.replaceChildren();
  const heading = document.createElement("strong");
  heading.textContent =
    score.percent === null
      ? "Match feedback unavailable"
      : `Visible resume match: ${score.percent}%`;
  const explanation = document.createElement("p");
  explanation.textContent = `${score.feedback} This is a keyword guide, not an employer ATS score.`;
  node.append(heading, explanation);
  if (score.missing_skills.length) {
    const missing = document.createElement("p");
    missing.textContent = `Named skills not shown: ${score.missing_skills.join(", ")}`;
    node.appendChild(missing);
  }
  if (score.missing_keypoints?.length) {
    const keypoints = document.createElement("p");
    keypoints.textContent = `Job terms not shown: ${score.missing_keypoints.join(", ")}`;
    node.appendChild(keypoints);
  }
}

function makeButton(label, callback, className = "dashboard-secondary") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", callback);
  return button;
}

function renderResumeEntries() {
  const { data } = documentSession;
  documentEntries.replaceChildren();
  data.state.visible_points ||= Object.fromEntries(
    (data.state.structure &&
      Object.values(data.state.structure)
        .flat()
        .map((entry) => [entry.id, [...entry.bullets]])) ||
      [],
  );
  data.state.visible_sections ||= Object.fromEntries(
    (data.sections || []).map((section) => [section.id, section.visible]),
  );
  const sections = document.getElementById("documentSections");
  sections.replaceChildren();
  for (const section of data.sections || []) {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = section.visible;
    checkbox.addEventListener("change", () => {
      section.visible = checkbox.checked;
      data.state.visible_sections[section.id] = checkbox.checked;
      scheduleLivePreview();
    });
    label.append(checkbox, document.createTextNode(` Show ${section.title}`));
    sections.appendChild(label);
  }
  for (const entry of data.entries) {
    const section = document.createElement("section");
    section.className = `document-entry${entry.visible ? "" : " is-hidden"}`;
    const heading = document.createElement("h4");
    heading.textContent = `${entry.category.replace("_", " ")} · ${entry.title}`;
    const toggleLabel = document.createElement("label");
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = entry.visible;
    toggle.setAttribute("aria-label", `Show ${entry.title}`);
    toggle.addEventListener("change", () => {
      entry.visible = toggle.checked;
      const selected = data.state.visible[entry.category];
      if (toggle.checked && !selected.includes(entry.id))
        selected.push(entry.id);
      if (!toggle.checked)
        data.state.visible[entry.category] = selected.filter(
          (id) => id !== entry.id,
        );
      section.classList.toggle("is-hidden", !toggle.checked);
      renderMatch({
        percent: null,
        missing_skills: [],
        feedback:
          "Save the draft to recalculate match feedback for the visible blocks.",
      });
      scheduleLivePreview();
    });
    toggleLabel.append(
      toggle,
      document.createTextNode(" Show in tailored resume"),
    );
    section.append(heading, toggleLabel);
    const points = document.createElement("div");
    points.className = "document-entry-points";
    for (const point of entry.bullets) {
      const row = document.createElement("div");
      row.className = `document-point${point.visible === false ? " is-hidden" : ""}`;
      const visibilityLabel = document.createElement("label");
      visibilityLabel.className = "document-point-visibility";
      const visibility = document.createElement("input");
      visibility.type = "checkbox";
      visibility.checked = point.visible !== false;
      visibility.addEventListener("change", () => {
        const selected =
          data.state.visible_points[
            data.state.structure[entry.category][Number(entry.id.split(":")[1])]
              .id
          ];
        if (!visibility.checked && selected.length <= 1 && entry.visible) {
          visibility.checked = true;
          documentMessage(
            "Keep at least one point in each visible block.",
            true,
          );
          return;
        }
        point.visible = visibility.checked;
        const id =
          data.state.structure[entry.category][Number(entry.id.split(":")[1])]
            .id;
        data.state.visible_points[id] = visibility.checked
          ? [...new Set([...selected, point.id])]
          : selected.filter((item) => item !== point.id);
        row.classList.toggle("is-hidden", !visibility.checked);
        scheduleLivePreview();
      });
      visibilityLabel.append(
        visibility,
        document.createTextNode(" Show this point"),
      );
      const label = document.createElement("label");
      label.textContent = "Resume point";
      const textarea = document.createElement("textarea");
      textarea.rows = 3;
      textarea.value = point.text;
      textarea.maxLength = 1000;
      textarea.dataset.pointId = point.id;
      textarea.addEventListener("input", () => {
        point.text = textarea.value;
        data.state.values[point.id] = textarea.value;
        scheduleLivePreview();
      });
      label.appendChild(textarea);
      const actions = document.createElement("div");
      actions.className = "document-actions";
      actions.append(
        makeButton("Ask AI about this point", () =>
          showPointPrompt(row, point),
        ),
      );
      actions.append(
        makeButton("Remove point", () => {
          if (entry.bullets.length <= 1) {
            documentMessage("Keep at least one point per block.", true);
            return;
          }
          const structureEntry =
            data.state.structure[entry.category][
              Number(entry.id.split(":")[1])
            ];
          if (
            entry.visible &&
            point.visible !== false &&
            data.state.visible_points[structureEntry.id].length <= 1
          ) {
            documentMessage(
              "Keep at least one visible point in this block.",
              true,
            );
            return;
          }
          entry.bullets = entry.bullets.filter((item) => item.id !== point.id);
          structureEntry.bullets = structureEntry.bullets.filter(
            (id) => id !== point.id,
          );
          data.state.visible_points[structureEntry.id] =
            data.state.visible_points[structureEntry.id].filter(
              (id) => id !== point.id,
            );
          delete data.state.values[point.id];
          renderResumeEntries();
          scheduleLivePreview();
        }),
      );
      row.append(visibilityLabel, label, actions);
      points.appendChild(row);
    }
    section.appendChild(points);
    section.appendChild(
      makeButton("+ Add point", () => {
        if (entry.bullets.length >= 20) {
          documentMessage("A block can have at most 20 points.", true);
          return;
        }
        const structureEntry =
          data.state.structure[entry.category][Number(entry.id.split(":")[1])];
        let index = 1;
        let id = `${structureEntry.id}_bullet_new_${index}`;
        while (Object.hasOwn(data.state.values, id))
          id = `${structureEntry.id}_bullet_new_${++index}`;
        entry.bullets.push({ id, text: "" });
        structureEntry.bullets.push(id);
        data.state.visible_points[structureEntry.id].push(id);
        data.state.values[id] = "";
        renderResumeEntries();
        scheduleLivePreview();
        documentEntries.querySelector(`[data-point-id="${id}"]`)?.focus();
      }),
    );
    documentEntries.appendChild(section);
  }
}

function showPointPrompt(row, point) {
  row.querySelector(".document-point-prompt")?.remove();
  const box = document.createElement("div");
  box.className = "document-point-prompt";
  const label = document.createElement("label");
  label.textContent = "What should AI improve about this point?";
  const input = document.createElement("textarea");
  input.rows = 2;
  label.appendChild(input);
  box.append(
    label,
    makeButton("Suggest revision", async () => {
      await requestResumeSuggestion(
        point.id,
        point.text,
        input.value || "Make this point clearer for the role.",
      );
    }),
  );
  row.appendChild(box);
  input.focus();
}

function showPointDiff(container, id, proposed) {
  const point = documentSession.data.entries
    .flatMap((entry) => entry.bullets)
    .find((item) => item.id === id);
  if (!point || !proposed.trim()) return;
  const card = document.createElement("div");
  card.className = "document-diff";
  const oldText = document.createElement("p");
  oldText.className = "document-diff-old";
  oldText.textContent = `− ${point.text}`;
  const label = document.createElement("label");
  label.textContent = "Proposed text — edit before accepting if needed";
  const newText = document.createElement("textarea");
  newText.rows = 3;
  newText.maxLength = 1000;
  newText.value = proposed.trim();
  label.appendChild(newText);
  card.append(
    oldText,
    label,
    makeButton(
      "Accept",
      () => {
        const field = documentEntries.querySelector(`[data-point-id="${id}"]`);
        if (!field) {
          documentMessage(
            "This point was removed. Request a new suggestion.",
            true,
          );
          card.remove();
          return;
        }
        point.text = newText.value;
        documentSession.data.state.values[id] = newText.value;
        field.value = newText.value;
        scheduleLivePreview();
        card.remove();
      },
      "dashboard-primary",
    ),
    makeButton("Reject", () => card.remove()),
  );
  container.appendChild(card);
}

async function requestResumeSuggestion(pointId, current, instruction) {
  const session = documentSession;
  documentMessage("Asking AI for a suggestion…");
  try {
    const result = await documentRequest(`${documentPath()}/suggest`, {
      method: "POST",
      body: JSON.stringify({
        point_id: pointId,
        current,
        instruction,
        mode: pointId ? "inline" : "suggestion",
      }),
    });
    if (session !== documentSession) return;
    const container = document.getElementById("documentSuggestions");
    if (pointId) {
      showPointDiff(container, pointId, result.proposal);
    } else {
      const parsed = JSON.parse(
        result.proposal.replace(/^```(?:json)?\s*|\s*```$/g, ""),
      );
      if (!Array.isArray(parsed.suggestions))
        throw new Error("AI returned an unsupported suggestion format.");
      let count = 0;
      for (const suggestion of parsed.suggestions.slice(0, 12)) {
        if (
          typeof suggestion.id === "string" &&
          typeof suggestion.text === "string"
        ) {
          showPointDiff(container, suggestion.id, suggestion.text);
          count++;
        }
      }
      if (!count)
        throw new Error("AI did not suggest changes to existing points.");
    }
    documentMessage("Review each suggested change before accepting it.");
  } catch (error) {
    if (session === documentSession) documentMessage(error.message, true);
  }
}

document.getElementById("resumeAskAi").addEventListener("click", () => {
  const instruction = document
    .getElementById("resumeAiInstruction")
    .value.trim();
  if (!instruction) {
    documentMessage("Describe the resume edit you want.", true);
    return;
  }
  const context = resumeProposalContext();
  if (!context) return;
  requestResumeSuggestion(null, context, instruction);
});
document.getElementById("resumeSuggest").addEventListener("click", () => {
  const context = resumeProposalContext();
  if (!context) return;
  requestResumeSuggestion(
    null,
    context,
    "Suggest truthful, job-relevant improvements to existing visible points.",
  );
});

function resumeProposalContext() {
  const selected = documentSession.data.entries.filter(
    (entry) => entry.visible,
  );
  for (let width = 500; width >= 31; width = Math.floor(width / 2)) {
    const context = JSON.stringify(
      selected.map((entry) => ({
        title: entry.title,
        points: entry.bullets.map((point) => ({
          id: point.id,
          text: point.text.slice(0, width),
        })),
      })),
    );
    if (context.length <= 11800) return context;
  }
  documentMessage(
    "This draft has too many points for one AI request. Ask AI about individual points.",
    true,
  );
  return null;
}

function showDocumentPdf(pdf, targetId = "documentPdf") {
  const previous = documentPdfBlobUrl;
  documentPdfBlobUrl = URL.createObjectURL(pdf);
  document.getElementById(targetId).src =
    `${documentPdfBlobUrl}#toolbar=0&navpanes=0&pagemode=none&view=FitH&zoom=page-width`;
  if (previous) URL.revokeObjectURL(previous);
}

async function refreshDocumentPdf(url) {
  const session = documentSession;
  const previewNumber = livePreviewNumber;
  try {
    const response = await fetch(
      `${url}${url.includes("?") ? "&" : "?"}v=${Date.now()}`,
    );
    if (session !== documentSession || previewNumber !== livePreviewNumber)
      return false;
    if (
      !response.ok ||
      !response.headers.get("Content-Type")?.includes("application/pdf")
    ) {
      throw new Error(
        "PDF preview is unavailable. Check that pdflatex is installed and the resume compiles.",
      );
    }
    const pdf = await response.blob();
    if (session !== documentSession || previewNumber !== livePreviewNumber)
      return false;
    showDocumentPdf(pdf);
    return true;
  } catch (error) {
    if (session === documentSession) documentMessage(error.message, true);
    return false;
  }
}

document.getElementById("resumeSave").addEventListener("click", async () => {
  const session = documentSession;
  cancelLivePreview();
  documentMessage("Saving tailored resume…");
  try {
    const result = await documentRequest(documentPath(), {
      method: "PUT",
      body: JSON.stringify({
        state: documentSession.data.state,
        version: documentSession.data.version,
      }),
    });
    if (session !== documentSession) return;
    documentSession.data.version = result.version;
    documentSession.data.score = result.score;
    renderMatch(result.score);
    const previewReady = await refreshDocumentPdf(documentSession.data.pdf_url);
    if (previewReady)
      documentMessage(
        "Tailored resume saved for this job. Autofill will use this version.",
      );
    loadApplications();
  } catch (error) {
    if (session === documentSession) documentMessage(error.message, true);
  }
});

document.getElementById("resumeReset").addEventListener("click", async () => {
  if (
    !window.confirm(
      "Replace this job's saved resume with a new draft from your current master? Existing job-specific edits will be lost.",
    )
  )
    return;
  const session = documentSession;
  cancelLivePreview();
  documentMessage("Creating a new draft…");
  try {
    const data = await documentRequest(`${documentPath()}/reset`, {
      method: "POST",
      body: "{}",
    });
    if (session !== documentSession) return;
    documentSession.data = data;
    renderResumeEntries();
    renderMatch(data.score);
    document.getElementById("documentSuggestions").replaceChildren();
    const previewReady = await refreshDocumentPdf(data.pdf_url);
    if (previewReady)
      documentMessage("New job-specific draft saved from your current master.");
    loadApplications();
  } catch (error) {
    if (session === documentSession) documentMessage(error.message, true);
  }
});

function showTextDiff(proposal, selection = null) {
  const container = document.getElementById("textSuggestions");
  container.replaceChildren();
  const card = document.createElement("div");
  card.className = "document-diff";
  const before = document.createElement("p");
  before.className = "document-diff-old";
  before.textContent = `− ${selection ? selection.current : document.getElementById("documentText").value}`;
  const label = document.createElement("label");
  label.textContent = "Proposed draft — edit before accepting if needed";
  const after = document.createElement("textarea");
  after.rows = 12;
  after.value = proposal;
  label.appendChild(after);
  card.append(
    before,
    label,
    makeButton(
      "Accept",
      () => {
        const editor = document.getElementById("documentText");
        if (selection) {
          if (editor.value !== selection.source) {
            documentMessage(
              "The draft changed since this suggestion. Request a new one.",
              true,
            );
            return;
          }
          editor.value =
            selection.source.slice(0, selection.start) +
            after.value +
            selection.source.slice(selection.end);
        } else editor.value = after.value;
        updateDocumentWordCount();
        card.remove();
      },
      "dashboard-primary",
    ),
    makeButton("Reject", () => card.remove()),
  );
  container.appendChild(card);
}

async function requestTextSuggestion(instruction, selection = null) {
  const session = documentSession;
  documentMessage("Asking AI for a suggestion…");
  try {
    const result = await documentRequest(`${documentPath()}/suggest`, {
      method: "POST",
      body: JSON.stringify({
        current: selection
          ? selection.current
          : document.getElementById("documentText").value,
        instruction,
        mode: selection ? "inline" : "suggestion",
      }),
    });
    if (session !== documentSession) return;
    showTextDiff(result.proposal, selection);
    documentMessage("Review the proposal and accept, edit, or reject it.");
  } catch (error) {
    if (session === documentSession) documentMessage(error.message, true);
  }
}

document.getElementById("documentAskAi").addEventListener("click", () => {
  const instruction = document
    .getElementById("documentInstruction")
    .value.trim();
  if (!instruction) {
    documentMessage("Describe the change you want.", true);
    return;
  }
  requestTextSuggestion(instruction);
});
document
  .getElementById("documentAskSelection")
  .addEventListener("click", () => {
    const editor = document.getElementById("documentText");
    const start = editor.selectionStart,
      end = editor.selectionEnd;
    if (start === end) {
      documentMessage("Select a sentence or paragraph to revise first.", true);
      return;
    }
    const instruction =
      document.getElementById("documentInstruction").value.trim() ||
      "Revise this selected text for the job while preserving truthful facts.";
    requestTextSuggestion(instruction, {
      source: editor.value,
      start,
      end,
      current: editor.value.slice(start, end),
    });
  });
document
  .getElementById("documentGenerate")
  .addEventListener("click", () =>
    requestTextSuggestion(
      "Generate a new truthful draft for this job. Keep placeholders for unknown facts.",
    ),
  );
document.getElementById("documentSave").addEventListener("click", async () => {
  const session = documentSession;
  try {
    await documentRequest(documentPath(), {
      method: "PUT",
      body: JSON.stringify({
        text: document.getElementById("documentText").value,
      }),
    });
    if (session !== documentSession) return;
    documentMessage(
      "Cover letter saved for this job. Autofill can attach its PDF.",
    );
    loadApplications();
  } catch (error) {
    if (session === documentSession) documentMessage(error.message, true);
  }
});
document.getElementById("documentCopy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(
      document.getElementById("documentText").value,
    );
    documentMessage("LinkedIn message copied.");
  } catch (error) {
    documentMessage(
      "Could not copy. Select the text and copy it manually.",
      true,
    );
  }
});
