const fillBtn = document.getElementById("fillBtn");
const startApplicationBtn = document.getElementById("startApplicationBtn");
const bookmarkBtn = document.getElementById("bookmarkBtn");
const dashboardBtn = document.getElementById("dashboardBtn");
const status = document.getElementById("status");
const nextJobPrompt = document.getElementById("nextJobPrompt");
const jobCardBody = document.getElementById("jobCardBody");
const statBookmarked = document.getElementById("statBookmarked");
const statApplied = document.getElementById("statApplied");
const statInterview = document.getElementById("statInterview");

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function loadTrackerStats() {
  chrome.runtime.sendMessage({ type: "GET_TRACKER_STATS" }, (response) => {
    if (!response || !response.ok) {
      statBookmarked.textContent = "–";
      statApplied.textContent = "–";
      statInterview.textContent = "–";
      return;
    }
    const { counts } = response.result;
    statBookmarked.textContent = counts.bookmarked;
    statApplied.textContent = counts.applied;
    statInterview.textContent = counts.interview;
  });
}

const PREVIEW_LENGTH = 220;

function loadJobPreview() {
  chrome.runtime.sendMessage({ type: "PREVIEW_JOB" }, (response) => {
    if (!response || !response.ok) {
      jobCardBody.innerHTML = `<span class="job-card-empty">Couldn't read this page — is the extension server running?</span>`;
      return;
    }
    const job = response.result;
    const fullDescription = job.description || "";
    const isTruncated = fullDescription.length > PREVIEW_LENGTH;
    const preview = fullDescription.slice(0, PREVIEW_LENGTH);

    jobCardBody.innerHTML = `
      <h2>${escapeHtml(job.title)}</h2>
      <div class="job-company">${escapeHtml(job.company)}${job.location ? " · " + escapeHtml(job.location) : ""}</div>
      <div class="job-description">${escapeHtml(preview) || "No description text detected on this page."}</div>
      ${isTruncated ? '<button type="button" class="job-card-expand" id="jobCardExpandBtn">Show full description</button>' : ""}
      <textarea id="jobCardFullDescription" readonly hidden>${escapeHtml(fullDescription)}</textarea>
      ${fullDescription ? '<button type="button" class="job-card-copy" id="jobCardCopyBtn" hidden>Copy description</button>' : ""}
    `;

    const expandBtn = document.getElementById("jobCardExpandBtn");
    const fullTextarea = document.getElementById("jobCardFullDescription");
    const copyBtn = document.getElementById("jobCardCopyBtn");

    if (expandBtn) {
      expandBtn.addEventListener("click", () => {
        fullTextarea.hidden = false;
        if (copyBtn) copyBtn.hidden = false;
        expandBtn.hidden = true;
      });
    }
    if (copyBtn) {
      copyBtn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(fullDescription);
          copyBtn.textContent = "Copied!";
        } catch (error) {
          fullTextarea.hidden = false;
          fullTextarea.select();
          copyBtn.textContent = "Press Ctrl/Cmd+C";
        }
        setTimeout(() => {
          copyBtn.textContent = "Copy description";
        }, 1800);
      });
    }
  });
}

function loadPageState() {
  chrome.runtime.sendMessage({ type: "GET_PAGE_STATE" }, (response) => {
    // Default to "Fill this page" on any error: it's the safer fallback
    // (a no-op click) versus guessing an Apply button exists.
    const showStartApplication = !!(response?.ok && response.result?.applyButtonText && !response.result.hasIdentityFields);
    startApplicationBtn.hidden = !showStartApplication;
    fillBtn.hidden = showStartApplication;
    if (showStartApplication && response.result.applyButtonText) {
      startApplicationBtn.textContent = `Start application ("${response.result.applyButtonText}")`;
    }
  });
}

loadTrackerStats();
loadJobPreview();
loadPageState();

dashboardBtn.addEventListener("click", () => {
  chrome.tabs.create({ url: "http://127.0.0.1:5050/dashboard" });
});

bookmarkBtn.addEventListener("click", () => {
  bookmarkBtn.disabled = true;
  chrome.runtime.sendMessage({ type: "BOOKMARK_JOB" }, (response) => {
    bookmarkBtn.disabled = false;
    if (!response || !response.ok) {
      status.innerHTML = `<span class="bad">Bookmark failed: ${
        (response && response.error) || "no response. Is the local server running?"
      }</span>`;
      return;
    }
    bookmarkBtn.textContent = "✓ Bookmarked";
    bookmarkBtn.classList.add("bookmarked");
    const r = response.result;
    status.innerHTML = `<span class="ok">Bookmarked:</span> ${escapeHtml(r.company)} — ${escapeHtml(r.title)}`;
    loadTrackerStats();
  });
});

startApplicationBtn.addEventListener("click", () => {
  startApplicationBtn.disabled = true;
  status.textContent = "Starting application…";

  chrome.runtime.sendMessage({ type: "START_APPLICATION" }, (response) => {
    startApplicationBtn.disabled = false;

    if (!response || !response.ok) {
      status.innerHTML = `<span class="bad">${escapeHtml((response && response.error) || "Couldn't start the application.")}</span>`;
      return;
    }

    status.innerHTML = `<span class="ok">✓ Clicked "${escapeHtml(response.result.text)}"</span> — reopen this popup once the form loads to fill it in.`;
    // The click may navigate or reveal the form in place. Either way,
    // re-check page state so the button switches to "Fill this page" once
    // the identity fields actually exist.
    setTimeout(loadPageState, 800);
    loadNextJobPrompt();
  });
});

function loadNextJobPrompt() {
  chrome.runtime.sendMessage({ type: "GET_NEXT_JOB" }, (response) => {
    if (!response || !response.ok || !response.result) {
      nextJobPrompt.innerHTML = "";
      return;
    }
    const next = response.result;
    nextJobPrompt.innerHTML = `
      <div class="next-job-prompt">
        <span class="next-job-label">Up next</span>
        <div class="next-job-title">${escapeHtml(next.title)}</div>
        <div class="next-job-company">${escapeHtml(next.company)}</div>
        <button type="button" id="openNextJobBtn">Open in a new tab</button>
      </div>
    `;
    document.getElementById("openNextJobBtn").addEventListener("click", () => {
      chrome.tabs.create({ url: next.url });
    });
  });
}

function renderAutofillSummary(result) {
  const aiSuggested = result.aiSuggested || [];
  const requiredAndUnmapped = new Set(result.stillEmptyRequired);
  const gateNote = result.gateClicked
    ? `<div class="autofill-gate-note">✓ Started application (clicked "${escapeHtml(result.gateClicked)}")</div>`
    : "";

  const headerText = result.unmapped.length
    ? `Autofill complete! <span class="review-count">${result.unmapped.length} field${result.unmapped.length === 1 ? "" : "s"} need review</span>`
    : "Autofill complete! All fields filled.";

  const reviewItems = result.unmapped
    .map(
      (label) =>
        `<li><span class="field-icon review">!</span> ${escapeHtml(label)}${
          requiredAndUnmapped.has(label) ? " <span class=\"bad\">(required)</span>" : ""
        }</li>`
    )
    .join("");
  const reviewSection = result.unmapped.length
    ? `<div class="autofill-section"><h3>Need to review (${result.unmapped.length})</h3><ul class="autofill-list">${reviewItems}</ul></div>`
    : "";

  const aiItems = aiSuggested.map((label) => `<li><span class="field-icon ai">🤖</span> ${escapeHtml(label)}</li>`).join("");
  const aiSection = aiSuggested.length
    ? `<div class="autofill-section"><h3>AI suggested — please verify (${aiSuggested.length})</h3><ul class="autofill-list">${aiItems}</ul></div>`
    : "";

  const completedItems = result.filled.map((label) => `<li><span class="field-icon done">✓</span> ${escapeHtml(label)}</li>`).join("");
  const completedSection = result.filled.length
    ? `<div class="autofill-section"><h3>Completed (${result.filled.length})</h3><ul class="autofill-list">${completedItems}</ul></div>`
    : "";

  const savedNote = result.applicationId
    ? '<p class="autofill-gate-note">Application saved in your tracker. Any generated resume stays with it.</p>'
    : "";
  const resumeNote = result.resumeError
    ? `<p class="bad" role="alert">Resume not uploaded: ${escapeHtml(result.resumeError)}</p>`
    : "";
  status.innerHTML = `${gateNote}${savedNote}${resumeNote}<div class="autofill-summary-header">${headerText}</div>${reviewSection}${aiSection}${completedSection}`;
}

fillBtn.addEventListener("click", () => {
  fillBtn.disabled = true;
  status.textContent = "Filling fields and preparing your resume…";

  chrome.runtime.sendMessage({ type: "FILL_THIS_PAGE" }, (response) => {
    fillBtn.disabled = false;

    if (!response || !response.ok) {
      status.innerHTML = `<span class="bad">Failed: ${
        escapeHtml((response && response.error) || "no response")
      }</span>`;
      return;
    }

    const r = response.result;
    if (!r) {
      status.innerHTML = `<span class="bad">No result — this page may not have a form.</span>`;
      return;
    }

    renderAutofillSummary(r);
  });
});
