/**
 * Service worker: injects the field scanner into a tab on demand, asks the
 * extension server's automation service how to answer detected fields,
 * and writes those answers back into the page.
 *
 * All fetch() calls live here (not in content.js) because a fetch from
 * content.js would be attributed to the PAGE's own origin. This service
 * worker's origin is the extension's privileged chrome-extension://,
 * which the host_permissions entries in manifest.json exempt from the
 * browser's CORS check entirely.
 *
 * Two separate backends, deliberately:
 *  - EXTENSION_SERVER_URL (extension_server.py, :8421): this
 *    workstream's own server for scraping and autofill matching. Nothing
 *    else on the team depends on it.
 *  - APP_SERVER_URL (app.py, :5050): the shared team app. The tracker
 *    API lives there since the dashboard and the kanban board work both
 *    need it too.
 *
 * Every fetch uses credentials: "include" so the session cookie set by
 * the app (localhost:5050) is sent along; the user must be logged in to
 * the app in a browser tab for the extension's tracker/autofill calls to
 * work.
 */
const EXTENSION_SERVER_URL = "http://127.0.0.1:8421";
const APP_SERVER_URL = "http://127.0.0.1:5050";

function logError(source, message, stack, url) {
  fetch(`${EXTENSION_SERVER_URL}/api/extension/log`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source,
      message: String(message),
      stack: stack ? String(stack) : null,
      url: url || null,
    }),
  }).catch(() => {});
}

// Every popup action (Fill this page, Bookmark, Start application, ...)
// gets a start/finish log line here, sent to the extension server's
// console output, so what the extension actually did is visible without
// relying on the popup UI (which disappears when the popup closes).
function logActivity(message) {
  fetch(`${EXTENSION_SERVER_URL}/api/extension/log`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source: "activity", message: String(message) }),
  }).catch(() => {});
}

// A server error (a 500, say) returns an HTML page, not JSON. Calling
// response.json() on that throws a confusing "Unexpected token '<'"
// instead of the real problem, so check response.ok against the raw text
// first and only parse JSON once we know it's actually JSON.
async function parseJsonOrThrow(response, fallbackMessage) {
  const rawText = await response.text();
  if (!response.ok) {
    let serverMessage = null;
    try {
      serverMessage = JSON.parse(rawText).error;
    } catch (e) {
      // Not JSON (an HTML error page, most likely). Fall through.
    }
    throw new Error(
      serverMessage ||
        `${fallbackMessage} (server returned status ${response.status})`,
    );
  }
  return JSON.parse(rawText);
}

self.addEventListener("error", (e) => {
  logError(
    "background",
    e.message,
    e.error && e.error.stack,
    e.filename ? `${e.filename}:${e.lineno}` : null,
  );
});
self.addEventListener("unhandledrejection", (e) => {
  logError(
    "background",
    e.reason && e.reason.message ? e.reason.message : String(e.reason),
    e.reason && e.reason.stack,
  );
});

// allFrames: true is required for sites that embed the whole candidate
// form in a same-origin iframe (confirmed on an iCIMS careers site: the
// top document never mentions "first name" at all, the entire form lives
// in a child iframe). Without this, injection only ever reaches the top
// frame and silently finds nothing on such sites.
async function injectScannerAndContentScript(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId, allFrames: true },
    files: ["field_scanner.js", "content.js"],
  });
}

// Runs `func` in every frame of the tab and returns only the frames that
// actually executed (a cross-origin or otherwise inaccessible frame is
// just absent from the result, not an error).
async function execInAllFrames(tabId, func, args = []) {
  try {
    return await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func,
      args,
    });
  } catch (e) {
    return [];
  }
}

// Matches "Upload Resume", "Attach your resume", generic "Upload Attachment",
// but never "Cover Letter", since uploading a resume as a cover letter
// would be answering wrong on purpose. Anything not caught by this stays
// unmapped for manual review, the same "don't guess" rule used everywhere
// else in this file.
const RESUME_FILE_LABEL_PATTERN = /r[ée]sum[ée]|resume|\bcv\b|curriculum vitae|attachment/i;
const COVER_LETTER_LABEL_PATTERN = /cover letter/i;

function arrayBufferToBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

async function getKnownJobForTab(tab) {
  let cachedJob = await getCachedJobForTab(tab.id);
  if (!cachedJob?.description?.trim()) {
    const response = await fetch(`${APP_SERVER_URL}/api/applications`, { credentials: "include" });
    const applications = await parseJsonOrThrow(response, "Could not load your bookmarked job.");
    const saved = applications.find((job) => job.url === (cachedJob?.url || tab.url));
    if (saved?.description?.trim()) {
      cachedJob = saved;
      await cacheJobForTab(tab.id, saved);
    }
  }
  return cachedJob;
}

async function uploadResumeToUnmappedFileFields(
  tab,
  scan,
  frameId,
  unmappedLabels,
) {
  const targetFields = scan.fields.filter(
    (f) =>
      f.kind === "file" &&
      unmappedLabels.includes(f.label) &&
      RESUME_FILE_LABEL_PATTERN.test(f.label) &&
      !COVER_LETTER_LABEL_PATTERN.test(f.label),
  );
  if (!targetFields.length) return [];

  const cachedJob = await getKnownJobForTab(tab);
  if (!cachedJob?.description?.trim()) {
    throw new Error("Open the job posting and choose Bookmark or Start application before filling your resume.");
  }
  // Each upload request gets a fresh ranking for this application's posting.
  const resumeResponse = await fetch(`${EXTENSION_SERVER_URL}/api/extension/tailor-resume`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "Accept": "application/pdf" },
    body: JSON.stringify({
      url: cachedJob.url,
      html: cachedJob.description,
      text: cachedJob.description,
      title: cachedJob.title || "Untitled role",
    }),
  });
  if (!resumeResponse.ok) {
    await parseJsonOrThrow(resumeResponse, "Could not tailor your resume.");
  }
  if (!resumeResponse.headers.get("Content-Type")?.includes("application/pdf")) {
    throw new Error("The server did not return a tailored PDF. Please retry.");
  }
  const filename = resumeResponse.headers.get("X-Resume-Type") === "master"
    ? "master_resume.pdf" : "tailored_resume.pdf";
  const base64Data = arrayBufferToBase64(await resumeResponse.arrayBuffer());

  const uploadedLabels = [];
  for (const field of targetFields) {
    const [{ result: resolvedId }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id, frameIds: [frameId] },
      func: (id, label) => window.__runJobApplyBotResolveFile(id, label),
      args: [field.id, field.label],
    });
    if (resolvedId === null || resolvedId === undefined) continue;
    const [{ result: wasSet }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id, frameIds: [frameId] },
      world: "MAIN",
      func: attachResumeFile,
      args: [resolvedId, base64Data, filename, "application/pdf"],
    });
    if (wasSet) uploadedLabels.push(field.label);
  }
  if (uploadedLabels.length !== targetFields.length) {
    throw new Error("The PDF was generated, but attachment could not be confirmed. Check the page for your filename before retrying.");
  }
  return uploadedLabels;
}

async function uploadCoverLetterToUnmappedFileFields(tab, scan, frameId, unmappedLabels, applicationId) {
  const targets = scan.fields.filter((field) => field.kind === "file" &&
    unmappedLabels.includes(field.label) && COVER_LETTER_LABEL_PATTERN.test(field.label));
  if (!targets.length) return [];
  const response = await fetch(`${EXTENSION_SERVER_URL}/api/applications/${applicationId}/documents/cover-letter.pdf`, {
    credentials: "include",
  });
  if (response.status === 404) return [];
  if (!response.ok) await parseJsonOrThrow(response, "Could not load the saved cover letter.");
  const data = arrayBufferToBase64(await response.arrayBuffer());
  const uploaded = [];
  for (const field of targets) {
    const [{ result: resolvedId }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id, frameIds: [frameId] },
      func: (id, label) => window.__runJobApplyBotResolveFile(id, label),
      args: [field.id, field.label],
    });
    if (resolvedId === null || resolvedId === undefined) continue;
    const [{ result: wasSet }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id, frameIds: [frameId] }, world: "MAIN",
      func: attachResumeFile,
      args: [resolvedId, data, "cover_letter.pdf", "application/pdf"],
    });
    if (wasSet) uploaded.push(field.label);
  }
  if (uploaded.length !== targets.length) {
    throw new Error("The cover letter PDF was generated, but attachment could not be confirmed.");
  }
  return uploaded;
}

// This function is serialized into the page's MAIN world. File and events
// must use that realm: Greenhouse ignores files created in an isolated world.
// Keep it self-contained; no extension APIs or page-provided helpers are used.
async function attachResumeFile(afId, base64Data, filename, mimeType) {
  const el = document.querySelector(`input[type="file"][data-af-id="${CSS.escape(String(afId))}"]`);
  if (!el || el.disabled) return false;
  try {
    const filenameWasVisible = document.body.innerText.includes(filename);
    const bytes = Uint8Array.from(atob(base64Data), char => char.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], filename, { type: mimeType }));
    el.files = transfer.files;
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    // ATS widgets can clear or replace the input before asynchronously
    // displaying an attachment chip. Give that transition time to finish.
    for (let attempt = 0; attempt < 50; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 100));
      const retainedFile = el.isConnected && el.files?.length === 1 &&
        el.files[0].name === filename && el.files[0].size === bytes.length;
      const attachmentAppeared = !filenameWasVisible && document.body.innerText.includes(filename);
      if (retainedFile || attachmentAppeared) return true;
    }
    return false;
  } catch (error) {
    return false;
  }
}

async function runFillOnTab(tab) {
  // Persist recovery state before scanning can click a navigation control or
  // tailoring/upload can fail. Existing applications keep their current stage.
  const application = await bookmarkTab(tab);
  await injectScannerAndContentScript(tab.id);

  // The real form may live in a same-origin iframe (confirmed on an
  // iCIMS careers site), not the top frame at all. Scan every frame and
  // treat whichever one found the most fields as "the" application form;
  // everything below (writing answers, uploading a resume) then targets
  // that same specific frame.
  const scanResults = await execInAllFrames(tab.id, () =>
    window.__runJobApplyBotScan(),
  );
  const validScans = scanResults.filter(
    (r) => r.result && r.result.fields && r.result.fields.length,
  );
  if (!validScans.length) return null;
  const best = validScans.sort(
    (a, b) => b.result.fields.length - a.result.fields.length,
  )[0];
  const scan = best.result;
  const frameId = best.frameId;

  const textFields = scan.fields.filter((f) => f.kind !== "file");
  const fileLabels = scan.fields.filter((f) => f.kind === "file").map((f) => f.label);
  const labels = textFields.map((f) => f.label);
  // Select/radio-group/checkbox-group fields carry their real option
  // text; sent along so the Tier 2 AI fallback (if available) can only
  // ever answer one of those choices verbatim, never invent one.
  const fieldOptions = {};
  for (const f of textFields) {
    if (f.options && f.options.length) fieldOptions[f.label] = f.options;
  }

  let filled = [];
  let aiSuggested = [];
  let unmapped = labels;

  if (labels.length) {
    try {
      const response = await fetch(
        `${EXTENSION_SERVER_URL}/api/extension/autofill-plan`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            field_labels: labels,
            field_options: fieldOptions,
          }),
        },
      );
      if (response.ok) {
        const plan = await response.json();
        filled = plan.filled;
        aiSuggested = plan.ai_suggested || [];
        unmapped = plan.unmapped;
      } else {
        logActivity(
          `autofill-plan returned status ${response.status}; leaving all fields unmapped`,
        );
      }
    } catch (e) {
      // Backend not running. Leave everything unmapped for manual review.
    }
  }

  // AI-suggested answers get written into the page exactly like a
  // Tier 1 match does, but are tracked under their own status the whole
  // way through, so the popup can show them as "please verify" rather
  // than "Completed" -- a guess is not the same confidence level as an
  // explicit rule, even once successfully written.
  const answers = filled
    .concat(aiSuggested)
    .map((f) => {
      const field = textFields.find((x) => x.label === f.label);
      return field
        ? { id: field.id, kind: field.kind, value: f.value, label: f.label }
        : null;
    })
    .filter(Boolean);
  const aiSuggestedLabels = new Set(aiSuggested.map((f) => f.label));

  // The server's plan says WHAT to answer; this only tells us what
  // actually got written into the DOM. A select with no matching option
  // text (our profile's value isn't one of that site's real choices) or a
  // group with no matching option label comes back here, and moves to
  // unmapped below instead of being reported as filled when it silently
  // did nothing.
  let successfulIds = [];
  if (answers.length) {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id, frameIds: [frameId] },
      func: (a) => window.__runJobApplyBotApplyAnswers(a),
      args: [answers],
    });
    successfulIds = result || [];
  }

  const successfulAnswers = answers.filter((a) => successfulIds.includes(a.id));
  const actuallyFilled = successfulAnswers.filter(
    (a) => !aiSuggestedLabels.has(a.label),
  );
  const actuallyAiSuggested = successfulAnswers.filter((a) =>
    aiSuggestedLabels.has(a.label),
  );
  const failedToWrite = answers
    .filter((a) => !successfulIds.includes(a.id))
    .map((a) => a.label);
  if (failedToWrite.length) {
    logActivity(
      `Matched but could not write into the page (no matching option?): ${failedToWrite.join(", ")}`,
    );
  }
  const trueUnmapped = unmapped.concat(failedToWrite, fileLabels);

  let uploadedLabels = [];
  let uploadedCoverLabels = [];
  let resumeError = null;
  try {
    uploadedLabels = await uploadResumeToUnmappedFileFields(
      tab,
      scan,
      frameId,
      trueUnmapped,
    );
  } catch (e) {
    resumeError = e.message || "Could not upload your tailored resume.";
  }
  try {
    uploadedCoverLabels = await uploadCoverLetterToUnmappedFileFields(
      tab, scan, frameId, trueUnmapped, application.id,
    );
  } catch (e) {
    resumeError = [resumeError, e.message || "Could not upload the cover letter."].filter(Boolean).join(" ");
  }
  const filledLabels = actuallyFilled
    .map((f) => f.label)
    .concat(uploadedLabels, uploadedCoverLabels);
  const aiSuggestedLabelsOut = actuallyAiSuggested.map((f) => f.label);
  const stillUnmapped = trueUnmapped.filter(
    (label) => !uploadedLabels.includes(label) && !uploadedCoverLabels.includes(label),
  );

  const stillEmptyRequired = scan.fields
    .filter((f) => f.required && stillUnmapped.includes(f.label))
    .map((f) => f.label);

  return {
    applicationId: application.id,
    gateClicked: scan.gateClicked,
    filled: filledLabels,
    aiSuggested: aiSuggestedLabelsOut,
    unmapped: stillUnmapped,
    stillEmptyRequired,
    resumeError,
  };
}

// Scrapes exactly the current page, no caching. Only call this when the
// current page is actually the right one to read from. See previewTab()
// and the caching functions below for why a fresh scrape is not always
// correct here.
async function scrapeCurrentPage(tab) {
  await injectScannerAndContentScript(tab.id);
  const results = await execInAllFrames(tab.id, () =>
    window.__runJobApplyBotCapturePage(),
  );
  const pages = results.map((r) => r.result).filter(Boolean);
  if (!pages.length)
    throw new Error("Could not read this page as a job posting.");
  // The real content usually lives in whichever frame captured the most
  // text; an empty/utility iframe alongside it captures next to nothing.
  const page = pages.reduce((longest, p) =>
    p.text.length > longest.text.length ? p : longest,
  );

  const scrapeResponse = await fetch(
    `${EXTENSION_SERVER_URL}/api/extension/scrape`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(page),
    },
  );
  return parseJsonOrThrow(
    scrapeResponse,
    "Could not read this page as a job posting.",
  );
}

async function detectPageState(tab) {
  await injectScannerAndContentScript(tab.id);
  const results = await execInAllFrames(tab.id, () =>
    window.__runJobApplyBotPageState(),
  );
  const states = results.map((r) => r.result).filter(Boolean);
  // hasIdentityFields winning in ANY frame means the real form has
  // rendered somewhere on this page, even if other frames (ads, trackers,
  // an empty utility iframe) report nothing.
  const withIdentity = states.find((s) => s.hasIdentityFields);
  if (withIdentity) return withIdentity;
  const withApplyButton = states.find((s) => s.applyButtonText);
  if (withApplyButton) return withApplyButton;
  return states[0] || { hasIdentityFields: false, applyButtonText: null };
}

// Clicking "Start application" navigates to a bare application form that
// often does not contain the job description at all anymore (confirmed on
// RBC's Workday careers site: the real posting text only exists on the
// page before the click). Caching keyed by tab ID is how the description
// survives that navigation for later Bookmark/Fill calls to reuse.
function jobCacheKey(tabId) {
  return `jobCache_${tabId}`;
}

async function cacheJobForTab(tabId, job) {
  await chrome.storage.local.set({ [jobCacheKey(tabId)]: job });
}

async function getCachedJobForTab(tabId) {
  const stored = await chrome.storage.local.get(jobCacheKey(tabId));
  return stored[jobCacheKey(tabId)] || null;
}

// hasIdentityFields() only checks the CURRENT page for a first-name field,
// which fails on a multi-step wizard: a step like "My experience" (job
// title, company, dates) legitimately has no first-name field even though
// the application is well underway (confirmed on RBC's multi-step apply
// flow). Once a tab is known to have started, that fact is remembered per
// tab so later steps are never mistaken for the original job-listing page
// again, regardless of what that particular step's fields look like.
function startedKey(tabId) {
  return `started_${tabId}`;
}

async function markApplicationStarted(tabId) {
  await chrome.storage.local.set({ [startedKey(tabId)]: true });
}

async function hasApplicationStarted(tabId) {
  const stored = await chrome.storage.local.get(startedKey(tabId));
  return !!stored[startedKey(tabId)];
}

// Separate from startedKey: "started" means the apply form was opened,
// "submitted" means a confirmation was actually detected afterwards. Kept
// per tab so checkForSubmissionConfirmation only ever fires its tracker
// update and banner once per tab, not on every subsequent page load.
function submittedKey(tabId) {
  return `submitted_${tabId}`;
}

async function markApplicationSubmitted(tabId) {
  await chrome.storage.local.set({ [submittedKey(tabId)]: true });
}

async function hasApplicationSubmitted(tabId) {
  const stored = await chrome.storage.local.get(submittedKey(tabId));
  return !!stored[submittedKey(tabId)];
}

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.local.remove([
    jobCacheKey(tabId),
    startedKey(tabId),
    submittedKey(tabId),
  ]);
});

// The single place that decides whether this tab is on the real
// application form: a fresh DOM check, or remembered state from an
// earlier step in the same tab if the DOM check alone says no.
async function getPageState(tab) {
  const pageState = await detectPageState(tab);
  if (pageState.hasIdentityFields) {
    await markApplicationStarted(tab.id);
    return pageState;
  }
  // A visible Apply control on a page without identity fields is stronger
  // evidence than remembered wizard state (Back navigation or another job).
  if (pageState.applyButtonText) {
    await chrome.storage.local.set({
      [startedKey(tab.id)]: false,
      [submittedKey(tab.id)]: false,
    });
    return pageState;
  }
  if (await hasApplicationStarted(tab.id)) {
    return { hasIdentityFields: true, applyButtonText: null };
  }
  return pageState;
}

// The single place that decides where to read the job posting from: the
// current page if it still has the description, or the cached copy from
// before "Start application" navigated away if not. Used for both the
// popup's preview and for bookmarking, so both always agree.
async function previewTab(tab) {
  const pageState = await getPageState(tab);

  if (pageState.hasIdentityFields) {
    const cached = await getCachedJobForTab(tab.id);
    if (cached) return cached;
    // No cached posting available (e.g. this tab arrived directly on an
    // application form); fall back to scraping this page even though the
    // result may be low quality here.
    return scrapeCurrentPage(tab);
  }

  // Still on the job posting page: the last reliable place to read the
  // real description, so cache it now for later use.
  const job = await scrapeCurrentPage(tab);
  await cacheJobForTab(tab.id, job);
  return job;
}

async function bookmarkTab(tab) {
  const known = await getKnownJobForTab(tab);
  const job = known?.description?.trim() ? known : await previewTab(tab);
  await cacheJobForTab(tab.id, job);

  const createResponse = await fetch(`${APP_SERVER_URL}/api/applications`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(job),
  });
  return parseJsonOrThrow(createResponse, "Could not save this application.");
}

async function startApplication(tab) {
  // Capture the description now, immediately before the click, since the
  // application form it navigates to may not contain it anymore.
  try {
    const job = await scrapeCurrentPage(tab);
    await cacheJobForTab(tab.id, job);
  } catch (e) {
    // Best-effort: a caching failure shouldn't block starting the application.
  }

  await injectScannerAndContentScript(tab.id);
  const results = await execInAllFrames(tab.id, () =>
    window.__runJobApplyBotStartApplication(),
  );
  const clicked = results.find((r) => r.result && r.result.clicked);
  if (!clicked) throw new Error("Couldn't find an Apply button on this page.");
  await markApplicationStarted(tab.id);
  return clicked.result;
}

// Suggests what to apply to next after starting one application, pulled
// from your own tracker's still-bookmarked jobs. No external scraping,
// and this never clicks anything itself. It only returns a URL for the
// popup to offer opening in a new tab; the real Submit button on that
// site is always still a manual, human click.
async function getNextBookmarkedJob(tab) {
  const response = await fetch(
    `${APP_SERVER_URL}/api/applications?stage=bookmarked`,
    {
      credentials: "include",
    },
  );
  const applications = await parseJsonOrThrow(
    response,
    "Could not reach the tracker.",
  );
  return (
    applications.find((application) => application.url !== tab.url) || null
  );
}

// Injected directly via chrome.scripting.executeScript's `func`, so it
// must be fully self-contained: no references to anything outside its
// own arguments, since the browser serializes only the function body.
function injectSubmissionBanner(nextJob) {
  if (document.getElementById("jobpilot-submission-banner")) return;

  const banner = document.createElement("div");
  banner.id = "jobpilot-submission-banner";
  banner.style.cssText =
    "position:fixed;top:16px;right:16px;z-index:2147483647;background:#164653;" +
    "color:#fff;padding:14px 18px;border-radius:10px;box-shadow:0 4px 18px rgba(0,0,0,0.25);" +
    "font-family:Arial,sans-serif;font-size:13px;max-width:280px;line-height:1.4;";

  const title = document.createElement("div");
  title.style.cssText = "font-weight:700;margin-bottom:4px;";
  title.textContent = "Application submitted";
  banner.appendChild(title);

  const body = document.createElement("div");
  body.style.cssText = "opacity:0.9;margin-bottom:10px;";
  body.textContent =
    "Detected a confirmation on this page. Marked as Applied in your tracker.";
  banner.appendChild(body);

  if (nextJob) {
    const nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.textContent = `Next: ${nextJob.title} @ ${nextJob.company}`;
    nextBtn.style.cssText =
      "display:block;width:100%;padding:8px;border:none;border-radius:7px;" +
      "background:#087f9a;color:#fff;font-weight:600;cursor:pointer;font-size:12px;margin-bottom:6px;";
    nextBtn.addEventListener("click", () => {
      window.open(nextJob.url, "_blank");
      banner.remove();
    });
    banner.appendChild(nextBtn);
  }

  const dismissBtn = document.createElement("button");
  dismissBtn.type = "button";
  dismissBtn.textContent = "Dismiss";
  dismissBtn.style.cssText =
    "display:block;width:100%;padding:6px;border:none;border-radius:7px;" +
    "background:transparent;color:#fff;opacity:0.75;cursor:pointer;font-size:11px;";
  dismissBtn.addEventListener("click", () => banner.remove());
  banner.appendChild(dismissBtn);

  document.body.appendChild(banner);
}

// Fires on every navigation/load in every tab (cheap: two storage reads,
// no network call, for the overwhelming majority of tabs that never
// started an application at all). Only tabs that had "Start application"
// clicked in them go on to the real check, and only once per tab.
async function checkForSubmissionConfirmation(tab) {
  if (!tab || !tab.id || !tab.url || !/^https?:/.test(tab.url)) return;
  if (await hasApplicationSubmitted(tab.id)) return;
  if (!(await hasApplicationStarted(tab.id))) return;

  let frameResults;
  try {
    await injectScannerAndContentScript(tab.id);
    frameResults = await execInAllFrames(tab.id, () =>
      window.__runJobApplyBotCheckSubmission(),
    );
  } catch (e) {
    return; // Frame not injectable (no host permission there): nothing to do.
  }
  const confirmed = frameResults.some((r) => r.result === true);
  if (!confirmed) return;


  let nextJob = null;
  try {
    const cachedJob = await getCachedJobForTab(tab.id);
    if (!cachedJob) return;
    const listResponse = await fetch(`${APP_SERVER_URL}/api/applications`, {
      credentials: "include",
    });
    const applications = await parseJsonOrThrow(listResponse, "Could not reach the tracker.");
    const match = applications.find((a) => a.url === cachedJob.url);
    if (!match || !["bookmarked", "applied"].includes(match.stage)) return;
    if (match.stage === "bookmarked") {
      const response = await fetch(`${APP_SERVER_URL}/api/applications/${match.id}/stage`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stage: "applied" }),
      });
      await parseJsonOrThrow(response, "Could not mark this application as applied.");
    }
    await markApplicationSubmitted(tab.id);
    logActivity(`Recorded a submission confirmation on ${tab.url}`);
  } catch (e) {
    logError("submission", e.message || String(e));
    return; // Leave this retryable; never claim a failed tracker write succeeded.
  }
  try {
    nextJob = await getNextBookmarkedJob(tab);
  } catch (e) {
    // Recording succeeded; a recommendation is optional.
  }

  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: injectSubmissionBanner,
      args: [nextJob],
    });
  } catch (e) {
    // The tab may have navigated away again already; not fatal.
  }
}

// The real Submit click always happens on the ATS's own site, outside
// this extension entirely; this is how a submission is noticed
// afterwards, by watching for the page it leaves you on, never by
// clicking anything itself.
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete") {
    checkForSubmissionConfirmation(tab).catch((e) =>
      logError("background", e.message, e.stack),
    );
  }
});

async function fetchTrackerStats() {
  const response = await fetch(`${APP_SERVER_URL}/api/applications`, {
    credentials: "include",
  });
  const applications = await parseJsonOrThrow(
    response,
    "Could not reach the tracker.",
  );
  const counts = {
    bookmarked: 0,
    applied: 0,
    interview: 0,
    offer: 0,
    rejected: 0,
  };
  for (const application of applications) {
    if (application.stage in counts) counts[application.stage] += 1;
  }
  return { total: applications.length, counts };
}

// `actionLabel` set means this is a real user button-press, worth a log
// line; PREVIEW_JOB/GET_PAGE_STATE fire automatically every time the
// popup opens and would just be noise, so they pass no label.
function withActiveTab(handler, sendResponse, actionLabel) {
  chrome.tabs.query({ active: true, currentWindow: true }, async ([tab]) => {
    try {
      if (!tab || !tab.id) throw new Error("No active tab is available.");
      if (actionLabel) logActivity(`${actionLabel}: started on ${tab.url}`);
      const result = await handler(tab);
      if (actionLabel) {
        const outcome = result?.resumeError ? `completed with resume error: ${result.resumeError}`
          : result?.unmapped?.length ? `completed with ${result.unmapped.length} fields needing review` : "succeeded";
        logActivity(`${actionLabel}: ${outcome} on ${tab.url}`);
      }
      sendResponse({ ok: true, result });
    } catch (e) {
      const message = e.message || String(e);
      if (actionLabel)
        logActivity(`${actionLabel}: FAILED on ${tab?.url || "unknown tab"}: ${message}`);
      sendResponse({ ok: false, error: message });
    }
  });
  return true; // keep the message channel open for the async response
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "FILL_THIS_PAGE") {
    return withActiveTab(runFillOnTab, sendResponse, "Fill this page");
  }
  if (message.type === "BOOKMARK_JOB") {
    return withActiveTab(bookmarkTab, sendResponse, "Bookmark");
  }
  if (message.type === "PREVIEW_JOB") {
    return withActiveTab(previewTab, sendResponse);
  }
  if (message.type === "GET_PAGE_STATE") {
    return withActiveTab(getPageState, sendResponse);
  }
  if (message.type === "START_APPLICATION") {
    return withActiveTab(startApplication, sendResponse, "Start application");
  }
  if (message.type === "GET_NEXT_JOB") {
    return withActiveTab(getNextBookmarkedJob, sendResponse);
  }
  if (message.type === "GET_TRACKER_STATS") {
    fetchTrackerStats()
      .then((result) => sendResponse({ ok: true, result }))
      .catch((e) => sendResponse({ ok: false, error: e.message || String(e) }));
    return true; // keep the message channel open for the async response
  }
  if (message.type === "EXTENSION_LOG") {
    logError(
      message.source || "content",
      message.message,
      message.stack,
      message.url,
    );
    return false;
  }
  return false;
});
