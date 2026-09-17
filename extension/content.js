/**
 * Defines the functions background.js injects and calls. Content
 * scripts run in the page's own origin, so this never fetches the backend
 * directly. That happens in background.js, which is why this file only
 * detects fields and writes values, and never decides an answer itself.
 *
 * Wrapped in an IIFE: background.js injects this file fresh on every
 * action (Fill, Bookmark, Start application all re-inject it into the
 * same page), and repeated injections share one persistent JS scope for
 * that page's lifetime. A top-level `const`/`let` would throw "already
 * been declared" on the second injection; scoping everything inside this
 * function means each injection gets its own fresh local scope instead,
 * while the window.__runJobApplyBotX assignments (reassignment, not
 * redeclaration) still update safely every time.
 */
(function () {
  window.__runJobApplyBotScan = function () {
    const scanner = window.AutofillScanner;
    const gateClicked = scanner.hasIdentityFields() ? null : scanner.clickAdvanceButton();
    const fields = scanner.scanIncompleteFields();

    return {
      gateClicked,
      fields: fields.map((f) => ({
        id: f.id,
        label: f.label,
        kind: f.kind,
        required: f.required,
        options: f.options,
      })),
    };
  };

  window.__runJobApplyBotApplyAnswers = function (answers) {
    const scanner = window.AutofillScanner;
    const filledIds = [];
    for (const { id, value, kind } of answers) {
      // Files are attached separately; a filename cannot be written as text.
      if (kind === "file") continue;
      try {
        let wasSet = false;
        if (kind === "radio-group" || kind === "checkbox-group") {
          wasSet = scanner.setGroupOption(id, value);
        } else {
          const el = scanner.getByAfId(id);
          if (el && el.type !== "file") {
            if (el.tagName === "SELECT") wasSet = scanner.setSelectByText(el, value);
            else if (kind === "single-checkbox") wasSet = scanner.setCheckbox(el, value);
            else {
              scanner.setNativeValue(el, value);
              wasSet = true;
            }
          }
        }
        if (wasSet) filledIds.push(id);
      } catch (e) {
        // A stale or unsupported control must not discard other successful writes.
      }
    }
    return filledIds;
  };

  // Real job-posting pages hide the description inside a specific content
  // region; the rest of the page (nav, header, cookie banners, "Sign up",
  // language pickers) is what dominates if we just grab all visible text.
  // Workday's data-automation-id is a confirmed, stable attribute; the rest
  // are generic fallbacks in decreasing order of confidence. Requires at
  // least 200 characters of text before trusting a match, so an empty or
  // near-empty wrapper (some sites nest their whole layout in a bare
  // <main>) doesn't win over a smaller, more specific real match later in
  // the list.
  const JOB_DESCRIPTION_SELECTORS = [
    '[data-automation-id="jobPostingDescription"]',
    '[class*="job-description" i]',
    '[id*="job-description" i]',
    '[class*="jobdescription" i]',
    '[id*="jobdescription" i]',
    '[class*="job-details" i]',
    '[id*="job-details" i]',
    "main",
    '[role="main"]',
    "article",
  ];
  const MIN_DESCRIPTION_LENGTH = 200;

  // The browser tab <title> is unreliable on client-rendered ATS pages: on
  // Workday postings it commonly stays a generic "Applying Job.. | Jobs at
  // <Company>" no matter which specific job is open, since the title never
  // gets updated after the initial page load. A real heading element is a
  // better source when one exists.
  const JOB_TITLE_SELECTORS = ['[data-automation-id="jobPostingHeader"]', "h1"];

  function findBestDescriptionText() {
    for (const selector of JOB_DESCRIPTION_SELECTORS) {
      const el = document.querySelector(selector);
      const text = el && el.innerText ? el.innerText.trim() : "";
      if (text.length >= MIN_DESCRIPTION_LENGTH) return text;
    }
    return document.body.innerText;
  }

  function findBestTitle() {
    for (const selector of JOB_TITLE_SELECTORS) {
      const el = document.querySelector(selector);
      const text = el && el.innerText ? el.innerText.trim() : "";
      if (text) return text;
    }
    return null;
  }

  window.__runJobApplyBotCapturePage = function () {
    return {
      html: document.documentElement.outerHTML,
      text: findBestDescriptionText(),
      title: findBestTitle(),
      url: window.location.href,
    };
  };

  // "Apply Now"-style wording, deliberately separate from field_scanner.js's
  // clickAdvanceButton() (which only matches Next/Continue and explicitly
  // excludes apply/submit wording, since that runs automatically during a
  // fill pass). This one only runs when the user explicitly clicks "Start
  // application" in the popup: an intentional action, not a side effect.
  const APPLY_BUTTON_PATTERNS = [
    "apply now",
    "apply for this job",
    "apply for this position",
    "apply to this position",
    "start application",
    "apply",
  ];

  function findApplyButton() {
    const scanner = window.AutofillScanner;
    for (const pattern of APPLY_BUTTON_PATTERNS) {
      const matches = document.evaluate(
        scanner.clickableXPath(pattern), document, null,
        XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null,
      );
      for (let i = 0; i < matches.snapshotLength; i++) {
        const el = matches.snapshotItem(i);
        if (scanner.isVisible(el) && !el.disabled && el.getAttribute("aria-disabled") !== "true") return el;
      }
    }
    return null;
  }

  // Read-only: lets the popup decide which button to show (has the real
  // application form rendered yet, or are we still on a job-description page
  // with just an Apply button) without clicking anything itself.
  window.__runJobApplyBotPageState = function () {
    const scanner = window.AutofillScanner;
    const applyButton = scanner.hasIdentityFields() ? null : findApplyButton();
    return {
      hasIdentityFields: scanner.hasIdentityFields(),
      applyButtonText: applyButton ? (applyButton.innerText || applyButton.value || "Apply").trim().slice(0, 60) : null,
    };
  };

  window.__runJobApplyBotStartApplication = function () {
    const button = findApplyButton();
    if (!button) return { clicked: false, text: null };
    const text = (button.innerText || button.value || "Apply").trim().slice(0, 60);
    button.click();
    return { clicked: true, text };
  };

  // Generic confirmation signals an ATS shows after a human clicks its own
  // real Submit button. Deliberately broad, non-site-specific phrasing,
  // since this workstream never controls or predicts which ATS a given
  // posting uses. Only ever read by background.js's own polling after
  // "Start application" was already clicked in this tab, never used to
  // trigger a submission itself.
  const SUBMISSION_TEXT_PATTERN =
    /thank you for (applying|your application)|application (has been |was )?(received|submitted)|we(?:'|)ve received your application|your application (has been submitted|was submitted)|submission (successful|received)|successfully submitted|application confirmation/i;
  const SUBMISSION_URL_PATTERN = /thank-?you|confirmation|application-?submitted|apply-?success/i;

  window.__runJobApplyBotCheckSubmission = function () {
    const scanner = window.AutofillScanner;
    const bodyText = (document.body && document.body.innerText) || "";
    if (SUBMISSION_TEXT_PATTERN.test(bodyText.slice(0, 4000))) return true;
    // A confirmation-shaped URL is a weaker signal on its own, so it only
    // counts once the identity fields (name, email, ...) are gone, i.e.
    // the form itself was actually replaced by something else.
    return SUBMISSION_URL_PATTERN.test(window.location.href) && !scanner.hasIdentityFields();
  };

  // Filling text can replace file inputs while the PDF is being fetched.
  window.__runJobApplyBotResolveFile = function (afId, label) {
    const scanner = window.AutofillScanner;
    let el = scanner.getByAfId(afId);
    if (!el || el.type !== "file") {
      const matches = scanner.scanIncompleteFields().filter(
        field => field.kind === "file" && field.label === label,
      );
      if (matches.length !== 1) return null;
      el = scanner.getByAfId(matches[0].id);
    }
    return el && el.type === "file" && !el.disabled ? el.getAttribute("data-af-id") : null;
  };
})();
