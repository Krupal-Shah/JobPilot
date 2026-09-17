const STAGES = ["bookmarked", "applied", "interview", "offer", "rejected"];

const applyAllBtn = document.getElementById("applyAllBtn");
const stopAutoApplyBtn = document.getElementById("stopAutoApplyBtn");
const addApplicationBtn = document.getElementById("addApplicationBtn");
const addApplicationForm = document.getElementById("addApplicationForm");
const cancelAddApplication = document.getElementById("cancelAddApplication");
const addApplicationStatus = document.getElementById("addApplicationStatus");
const boardStatus = document.getElementById("boardStatus");

const modal = document.getElementById("expandModal");
const modalCloseBtn = document.getElementById("modalCloseBtn");
const modalStageBadge = document.getElementById("modalStageBadge");
const modalTitle = document.getElementById("modalTitle");
const modalCompany = document.getElementById("modalCompany");
const modalDescription = document.getElementById("modalDescription");
const modalCopyBtn = document.getElementById("modalCopyBtn");
const modalViewPosting = document.getElementById("modalViewPosting");
const modalViewResume = document.getElementById("modalViewResume");
const modalResumeStatus = document.getElementById("modalResumeStatus");
const modalRemoveBtn = document.getElementById("modalRemoveBtn");

let applications = [];
let bookmarkedApps = null;
let draggedApplicationId = null;
const movingApplications = new Set();
let activeDescription = "";

function stageLabel(stage) {
  return stage.charAt(0).toUpperCase() + stage.slice(1);
}

async function loadApplications() {
  try {
    const sortSelect = document.getElementById('sortSelect');
    const sortMode = (sortSelect && sortSelect.value) || localStorage.getItem('dashboard.sort') || 'best';

    // Fetch all apps (used for non-bookmarked stages) and fetch bookmarked separately with sort.
    const [allResp, bookmarkedResp] = await Promise.all([
      fetch('/api/applications'),
      fetch(`/api/applications?stage=bookmarked&sort=${encodeURIComponent(sortMode)}`),
    ]);
    if (!allResp.ok || !bookmarkedResp.ok) throw new Error('Request failed');
    const allApps = await allResp.json();
    const bk = await bookmarkedResp.json();

    // Keep full list for non-bookmarked stages, but store bookmarked apps separately
    applications = allApps;
    bookmarkedApps = bk;
    boardStatus.textContent = "";
    boardStatus.className = "board-status";
    renderBoard();
  } catch (error) {
    boardStatus.textContent = "Could not load applications from the server.";
    boardStatus.className = "board-status bad";
  }
}

// Persist sort selection and reload bookmarked column when changed.
const sortSelectEl = document.getElementById('sortSelect');
if (sortSelectEl) {
  // initialize from localStorage
  const saved = localStorage.getItem('dashboard.sort');
  if (saved) sortSelectEl.value = saved;
  sortSelectEl.addEventListener('change', () => {
    localStorage.setItem('dashboard.sort', sortSelectEl.value);
    loadApplications();
  });
}

function renderBoard() {
  for (const stage of STAGES) {
    const column = document.getElementById(`column-${stage}`);
    const count = document.getElementById(`count-${stage}`);
    let stageApplications;
    if (stage === 'bookmarked' && Array.isArray(bookmarkedApps)) {
      stageApplications = bookmarkedApps;
    } else {
      stageApplications = applications.filter((application) => application.stage === stage);
    }
    count.textContent = stageApplications.length;

    column.innerHTML = "";
    if (!stageApplications.length) {
      const empty = document.createElement("div");
      empty.className = "kanban-empty";
      empty.textContent = "No applications here yet.";
      column.appendChild(empty);
      continue;
    }
    for (const application of stageApplications) {
      column.appendChild(buildCard(application));
    }
  }
}

function buildCard(application) {
  const card = document.createElement("article");
  card.className = "kanban-card";
  card.draggable = true;
  card.tabIndex = 0;
  card.setAttribute("aria-label", `View ${application.title}; use left or right arrow to move stages`);
  card.addEventListener("keydown", (event) => {
    if (event.target === card && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      openModal(application);
    } else if (event.target === card && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
      event.preventDefault();
      const offset = event.key === "ArrowRight" ? 1 : -1;
      const next = STAGES[STAGES.indexOf(application.stage) + offset];
      if (next) updateStage(application.id, next, card);
    }
  });
  card.dataset.applicationId = application.id;

  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "kanban-card-remove";
  removeBtn.setAttribute("aria-label", `Remove ${application.title}`);
  removeBtn.textContent = "×";
  removeBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    removeApplication(application.id, application.title);
  });

  const heading = document.createElement("h3");
  heading.textContent = application.title;

  const company = document.createElement("p");
  company.className = "application-company";
  company.textContent = application.location
    ? `${application.company} · ${application.location}`
    : application.company;

  card.append(removeBtn, heading, company);

  if (application.description) {
    const description = document.createElement("p");
    description.className = "kanban-card-description";
    description.textContent = application.description;
    card.appendChild(description);
  }

  const documentActions = document.createElement("div");
  documentActions.className = "kanban-doc-actions";
  for (const [kind, label] of [["resume", "Tailor resume"],
                               ["cover_letter", "Cover letter"],
                               ["cold_email", "Cold email"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openDocumentEditor(application, kind);
    });
    documentActions.appendChild(button);
  }
  card.appendChild(documentActions);

  card.addEventListener("dragstart", () => {
    draggedApplicationId = application.id;
    card.classList.add("dragging");
  });
  card.addEventListener("dragend", () => {
    card.classList.remove("dragging");
    draggedApplicationId = null;
  });
  card.addEventListener("click", () => openModal(application));

  return card;
}

async function removeApplication(applicationId, title) {
  if (!window.confirm(`Remove "${title}" from your tracker? This can't be undone.`)) return;

  try {
    const response = await fetch(`/api/applications/${applicationId}`, { method: "DELETE" });
    if (!response.ok && response.status !== 204) throw new Error("Delete failed");
    applications = applications.filter((application) => application.id !== applicationId);
    bookmarkedApps = bookmarkedApps?.filter((application) => application.id !== applicationId);
    renderBoard();
    if (!modal.hidden && Number(modal.dataset.applicationId) === applicationId) closeModal();
  } catch (error) {
    boardStatus.textContent = "Could not remove that application. Please try again.";
    boardStatus.className = "board-status bad";
  }
}

function openModal(application) {
  modal.dataset.applicationId = application.id;
  modalStageBadge.textContent = stageLabel(application.stage);
  modalStageBadge.className = `stage-badge stage-${application.stage}`;
  modalTitle.textContent = application.title;
  modalCompany.textContent = application.location
    ? `${application.company} · ${application.location}`
    : application.company;
  activeDescription = application.description || "";
  renderDescription(activeDescription, modalDescription);
  modalViewPosting.href = application.url;
  if (modalViewResume && modalResumeStatus) {
    modalViewResume.hidden = !application.resume_pdf_url;
    if (application.resume_pdf_url) modalViewResume.href = application.resume_pdf_url;
    else modalViewResume.removeAttribute("href");
    modalResumeStatus.textContent = application.resume_pdf_url
      ? "The saved resume will be compiled when you open it."
      : application.tailoring_error || "Open Tailor resume to create a draft.";
  }
  modal.hidden = false;
  modal._returnFocus = document.activeElement;
  modalCloseBtn.focus();
}

function closeModal() {
  modal.hidden = true;
  modal._returnFocus?.focus();
}

modalCloseBtn.addEventListener("click", closeModal);
modal.addEventListener("click", (event) => {
  if (event.target === modal) closeModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Tab" && !modal.hidden) {
    const controls = [...modal.querySelectorAll('button, a[href], input, textarea')].filter(el => !el.hidden && !el.disabled);
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  if (event.key === "Escape" && !modal.hidden) closeModal();
});

modalCopyBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(activeDescription);
    modalCopyBtn.textContent = "Copied!";
  } catch (error) {
    modalCopyBtn.textContent = "Copy unavailable";
  }
  setTimeout(() => {
    modalCopyBtn.textContent = "Copy description";
  }, 1800);
});

function renderDescription(raw, container) {
  container.replaceChildren();
  if (!raw.trim()) {
    const empty = document.createElement("p");
    empty.textContent = "No description was captured for this posting.";
    container.appendChild(empty);
    return;
  }
  const lines = raw.replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [];
  let list = null;
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const node = document.createElement("p");
    node.textContent = paragraph.join(" ").trim();
    container.appendChild(node);
    paragraph = [];
  };
  for (const source of lines) {
    const line = source.trim();
    if (!line) { flushParagraph(); list = null; continue; }
    const bullet = line.match(/^(?:[-*•]|\d+[.)])\s+(.+)/);
    if (bullet) {
      flushParagraph();
      if (!list) {
        list = document.createElement("ul");
        container.appendChild(list);
      }
      const item = document.createElement("li");
      item.textContent = bullet[1];
      list.appendChild(item);
      continue;
    }
    list = null;
    const heading = line.replace(/^#{1,3}\s*/, "").replace(/:$/, "");
    const knownHeading = /^(?:about (?:us|the role)|overview|responsibilities|what you(?:'ll| will) do|requirements|qualifications|what we(?:'re| are) looking for|benefits|perks|salary|compensation|how to apply)$/i.test(heading);
    if ((line.endsWith(":") && line.length < 80) || knownHeading || /^#{1,3}\s+/.test(line)) {
      flushParagraph();
      const node = document.createElement("h3");
      node.textContent = heading;
      container.appendChild(node);
    } else {
      paragraph.push(line);
    }
  }
  flushParagraph();
}

modalRemoveBtn.addEventListener("click", () => {
  const applicationId = Number(modal.dataset.applicationId);
  const application = applications.find((a) => a.id === applicationId);
  removeApplication(applicationId, application ? application.title : "this application");
});

for (const stage of STAGES) {
  const column = document.getElementById(`column-${stage}`);
  column.addEventListener("dragover", (event) => {
    event.preventDefault();
    column.classList.add("drag-over");
  });
  column.addEventListener("dragleave", (event) => {
    if (!column.contains(event.relatedTarget)) column.classList.remove("drag-over");
  });
  column.addEventListener("drop", async (event) => {
    event.preventDefault();
    column.classList.remove("drag-over");
    if (draggedApplicationId === null) return;
    const card = document.querySelector(`[data-application-id="${draggedApplicationId}"]`);
    await updateStage(draggedApplicationId, stage, card);
  });
}

async function updateStage(applicationId, stage, card) {
  const application = applications.find((a) => a.id === applicationId);
  if (!application || !card || !STAGES.includes(stage) ||
      application.stage === stage || movingApplications.has(applicationId)) return;
  const previousStage = application.stage;
  movingApplications.add(applicationId);
  application.stage = stage;
  bookmarkedApps = bookmarkedApps?.filter((item) => item.id !== applicationId);
  if (stage === "bookmarked" && bookmarkedApps) bookmarkedApps.unshift(application);
  const target = document.getElementById(`column-${stage}`);
  target.querySelector(".kanban-empty")?.remove();
  target.prepend(card);
  card.classList.remove("card-arrived");
  void card.offsetWidth;
  card.classList.add("card-arrived");
  for (const currentStage of STAGES) {
    const column = document.getElementById(`column-${currentStage}`);
    const count = column.querySelectorAll(".kanban-card").length;
    document.getElementById(`count-${currentStage}`).textContent = count;
    if (!count && !column.querySelector(".kanban-empty")) {
      const empty = document.createElement("div");
      empty.className = "kanban-empty";
      empty.textContent = "No applications here yet.";
      column.appendChild(empty);
    }
  }
  boardStatus.textContent = `Moving ${application.title} to ${stageLabel(stage)}…`;
  boardStatus.className = "board-status";

  try {
    const response = await fetch(`/api/applications/${applicationId}/stage`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage }),
    });
    if (!response.ok) throw new Error("Update failed");
    boardStatus.textContent = `Moved ${application.title} to ${stageLabel(stage)}.`;
  } catch (error) {
    application.stage = previousStage;
    if (previousStage === "bookmarked" && bookmarkedApps) bookmarkedApps.unshift(application);
    else bookmarkedApps = bookmarkedApps?.filter((item) => item.id !== applicationId);
    renderBoard();
    boardStatus.textContent = "Could not move that card. Please try again.";
    boardStatus.className = "board-status bad";
  } finally {
    movingApplications.delete(applicationId);
  }
}

addApplicationBtn.addEventListener("click", () => {
  addApplicationForm.hidden = !addApplicationForm.hidden;
});

function setAutoApplyRunning(running) {
  applyAllBtn.hidden = running;
  stopAutoApplyBtn.hidden = !running;
}

// apply.py never exits on its own once a job needs review (by design, to
// keep the browser open for you) -- reflects that on page load so a
// refresh doesn't lose track of a run already in progress.
(async () => {
  try {
    const response = await fetch("/api/automation/status");
    const data = await response.json().catch(() => ({}));
    setAutoApplyRunning(!!(response.ok && data.running));
  } catch (error) {
    // Best-effort; leave the default (not running) state shown.
  }
})();

applyAllBtn.addEventListener("click", async () => {
  if (!window.confirm("This opens its own Chrome window and starts filling your bookmarked jobs one at a time. Nothing gets submitted without you reviewing and clicking submit yourself. Continue?")) return;

  applyAllBtn.disabled = true;
  boardStatus.textContent = "Launching auto-apply...";
  boardStatus.className = "board-status";
  try {
    const response = await fetch("/api/automation/apply-all", { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Could not start auto-apply.");
    boardStatus.textContent = "Auto-apply running in its own Chrome window -- check there to review and submit each application.";
    boardStatus.className = "board-status";
    setAutoApplyRunning(true);
  } catch (error) {
    boardStatus.textContent = error.message || "Could not start auto-apply. Please try again.";
    boardStatus.className = "board-status bad";
  } finally {
    applyAllBtn.disabled = false;
  }
});

stopAutoApplyBtn.addEventListener("click", async () => {
  if (!window.confirm("Stop auto-apply and close its Chrome window? Anything left unreviewed stays bookmarked.")) return;

  stopAutoApplyBtn.disabled = true;
  try {
    const response = await fetch("/api/automation/stop", { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Could not stop auto-apply.");
    boardStatus.textContent = "Auto-apply stopped.";
    boardStatus.className = "board-status";
    setAutoApplyRunning(false);
  } catch (error) {
    boardStatus.textContent = error.message || "Could not stop auto-apply. Please try again.";
    boardStatus.className = "board-status bad";
  } finally {
    stopAutoApplyBtn.disabled = false;
  }
});

cancelAddApplication.addEventListener("click", () => {
  addApplicationForm.hidden = true;
  addApplicationForm.reset();
});

addApplicationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(addApplicationForm);
  const payload = Object.fromEntries(formData.entries());

  try {
    const response = await fetch("/api/applications", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      addApplicationStatus.textContent = data.error || "Could not save application.";
      addApplicationStatus.className = "bad";
      return;
    }
    addApplicationStatus.textContent = "Saved.";
    addApplicationStatus.className = "ok";
    addApplicationForm.reset();
    addApplicationForm.hidden = true;
    await loadApplications();
  } catch (error) {
    addApplicationStatus.textContent = "Could not reach the server.";
    addApplicationStatus.className = "bad";
  }
});

loadApplications();
