/**
 * Field-discovery engine for the job-application autofill extension.
 *
 * Pure vanilla JS, no build step, no external dependency. Safe to load
 * as a Manifest V3 content script (content scripts run in an isolated
 * world, so this never collides with the host page's own JS).
 *
 * This file only detects and writes form fields; it never decides WHAT to
 * put in them. Answer-matching (which profile field or screening answer a
 * given label means) lives server-side in services/automation_service.py,
 * so extending the rules only means editing the profile, not this file.
 *
 * Native value setters + dispatched input/change events are used instead
 * of a plain `el.value = x` assignment, since React/Vue-controlled inputs
 * otherwise silently ignore the write. Their framework only reacts to
 * the same setter path a real keystroke would trigger.
 */

window.AutofillScanner = (function () {
  "use strict";

  // ---- shared low-level helpers -------------------------------------

  function setNativeValue(el, value) {
    const proto =
      el.tagName === "TEXTAREA"
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setSelectByText(selectEl, wantedText) {
    const wanted = wantedText.trim().toLowerCase();
    const opt = Array.from(selectEl.options).find(
      (o) => o.text.trim().toLowerCase() === wanted
    );
    if (!opt) return false;
    selectEl.value = opt.value;
    selectEl.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  // Checkboxes/radios are answered by their .checked state, not .value --
  // setNativeValue() never touches .checked, so a checkbox field silently
  // did nothing under the old text-only write path. "Yes"/"true"/"1"
  // (case-insensitive) check it; anything else, including an empty
  // answer, leaves/sets it unchecked. A real toggle, not a guess.
  function setCheckbox(el, wantedValue) {
    const shouldBeChecked = /^(yes|true|1)$/i.test(String(wantedValue).trim());
    if (el.checked === shouldBeChecked) return true;
    el.checked = shouldBeChecked;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  // For a radio-group/checkbox-group: finds the specific option whose own
  // label text matches the answer and checks that one. `groupName` is the
  // shared `name` attribute apply.py-style groups are keyed by; falls back
  // to matching by nearby label text for Workday-style groups with no
  // shared `name` at all (see scanIncompleteFields' groupQuestionLabel).
  function setGroupOption(afId, wantedValue) {
    const group = document.querySelectorAll(`[data-af-group="${afId}"]`);
    const wanted = String(wantedValue).trim().toLowerCase();
    for (const el of group) {
      const label = (el.dataset.afOptionLabel || "").trim().toLowerCase();
      if (label === wanted) {
        el.checked = true;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      }
    }
    return false;
  }

  // Case-insensitive "contains" XPath predicate against an element's full
  // descendant string-value ("."). Matches button/label text wrapped in
  // nested <span>/<strong>/etc, which most styled UI components do.
  function lowerXPath(text) {
    const clean = text.replace(/'/g, "");
    return (
      "contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', " +
      "'abcdefghijklmnopqrstuvwxyz'), '" +
      clean.toLowerCase() +
      "')"
    );
  }

  // Same idea, scoped to an element's own DIRECT text() node children only.
  // Use this for broad "//*[...]" whole-document scans: lowerXPath's "."
  // bubbles all the way up an ancestor chain, so //*[contains(., 'x')]
  // matches <html>/<body> themselves whenever "x" appears anywhere on the
  // page, even inert JSON inside a <script> tag (that false match made a
  // "has the form rendered" check report true before the form ever
  // existed on one React-heavy site). <html>/<body> hold zero direct
  // text nodes, only more elements, so they can never match this way.
  function lowerXPathOwnText(text) {
    const clean = text.replace(/'/g, "");
    return (
      "text()[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', " +
      "'abcdefghijklmnopqrstuvwxyz'), '" +
      clean.toLowerCase() +
      "')]"
    );
  }

  // Matches button/a/role=button text, OR input[type=button|submit] via
  // @value, OR input[type=image] via @alt. Growable matcher; extend here
  // (and nowhere else) if a new "looks like a button but isn't a
  // <button>" shape gets confirmed.
  function clickableXPath(pattern) {
    const textMatch = lowerXPath(pattern);
    const valueMatch = textMatch.replace("translate(.,", "translate(@value,");
    const altMatch = textMatch.replace("translate(.,", "translate(@alt,");
    return (
      `//button[${textMatch}] | ` +
      `//a[${textMatch}] | ` +
      `//*[@role='button'][${textMatch}] | ` +
      `//input[(@type='button' or @type='submit') and ${valueMatch}] | ` +
      `//input[@type='image' and ${altMatch}]`
    );
  }

  function firstXPathMatch(xpath, root) {
    const result = document.evaluate(
      xpath,
      root || document,
      null,
      XPathResult.FIRST_ORDERED_NODE_TYPE,
      null
    );
    return result.singleNodeValue;
  }

  function isVisible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
    const style = window.getComputedStyle(el);
    return style.display !== "none" && style.visibility !== "hidden";
  }

  // Broadens "what counts as a label" beyond the <label> tag. Some custom
  // form builders put a field's visible text in a plain span or div,
  // associated to its input purely via aria-labelledby, never <label
  // for>, wrapping, or DOM adjacency. This is a standard ARIA pattern, so
  // growing the label-detection here covers any site built that way.
  // Real <label> elements still come first so existing for=/wrapping
  // resolution keeps taking priority when both exist.
  function queryLabelLikeElements() {
    const real = Array.from(document.querySelectorAll("label"));
    const realSet = new Set(real);
    const ariaLabelIds = new Set();
    document.querySelectorAll("[aria-labelledby]").forEach((el) => {
      el.getAttribute("aria-labelledby")
        .split(/\s+/)
        .forEach((id) => {
          if (id) ariaLabelIds.add(id);
        });
    });
    const ariaLabelEls = Array.from(ariaLabelIds)
      .map((id) => document.getElementById(id))
      .filter((el) => el && !realSet.has(el));
    return real.concat(ariaLabelEls);
  }

  // Given a label-like element (a real <label> OR an aria-labelledby
  // target span/div), returns the field it labels. Resolved via for=,
  // via aria-labelledby reverse lookup (attribute-list-safe, since
  // aria-labelledby can hold multiple space-separated ids), or via
  // nesting inside a real <label>.
  function fieldForLabelLikeElement(el) {
    const forAttr = el.getAttribute("for");
    if (forAttr) {
      const target = document.getElementById(forAttr);
      if (target) return target;
    }
    if (el.id) {
      const target = document.querySelector(`[aria-labelledby~="${el.id}"]`);
      if (target) return target;
    }
    if (el.tagName === "LABEL") {
      const nested = el.querySelector("input, select, textarea");
      if (nested) return nested;
    }
    return null;
  }

  // True once the real candidate form (not just a landing/gate page) has
  // rendered, checked via ground-truth form-control attributes, not page
  // text (see lowerXPathOwnText's docstring for why a text-based version
  // is unsafe).
  function hasIdentityFields() {
    const selectors = [
      'input[name="first_name"]',
      'input[name*="first" i][name*="name" i]',
      'input[id*="first" i][id*="name" i]',
      'input[placeholder*="first name" i]',
      'input[aria-label*="first name" i]',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && isVisible(el)) return true;
    }
    const labelXPath = `//label[${lowerXPathOwnText("first name")}]`;
    const label = firstXPathMatch(labelXPath);
    return isVisible(label);
  }

  // Finds and clicks a "Next"/"Continue"/"Save and Continue"/"Parse
  // CV"-style button. Deliberately excludes "submit"/"apply" wording so
  // this can never be the final-submission click.
  function clickAdvanceButton() {
    const patterns = [
      "next",
      "continue",
      "save and continue",
      "save & continue",
      "proceed",
      "parse cv",
      "parse resume",
    ];
    for (const pattern of patterns) {
      const el = firstXPathMatch(clickableXPath(pattern));
      if (el && isVisible(el)) {
        el.click();
        return pattern;
      }
    }
    return null;
  }

  // ---- dropdown discovery --------------------------------------------

  // Clicks a field open and reads back whatever role="option" elements
  // actually render, purely from the live DOM, no hardcoded option lists.
  // Takes a before/after snapshot and keeps only options that are
  // genuinely new since the click, so a stale, already-selected
  // chip/option sitting anywhere else on the page never gets misread as
  // this field's own options.
  function scanOptionTexts() {
    return Array.from(
      document.querySelectorAll(
        '[role="option"], [role="listbox"] li, [role="listbox"] div'
      )
    )
      .map((el) => (el.innerText || "").trim())
      .filter((t) => t && t.length > 0 && t.length < 200);
  }

  function discoverDropdownOptions(el) {
    const before = new Set(scanOptionTexts());
    try {
      el.click();
    } catch (e) {
      return [];
    }
    // Synchronous-ish peek; caller should re-check shortly after if the
    // options render asynchronously. Wrap this in a small setTimeout if a
    // given site's options are slow to render.
    const after = scanOptionTexts();
    try {
      document.activeElement && document.activeElement.blur();
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true })
      );
    } catch (e) {
      /* best-effort close */
    }
    const seen = new Set();
    const deduped = [];
    for (const o of after) {
      if (!before.has(o) && !seen.has(o)) {
        seen.add(o);
        deduped.push(o);
      }
    }
    return deduped;
  }

  // ---- main field scan -------------------------------------------------

  // Groups related checkboxes/radios, applies generic-label fallback
  // heuristics, and tags each field with a data-af-id so it can be
  // re-targeted precisely after being surfaced.
  function scanIncompleteFields() {
    const out = [];
    const seenGroups = new Set();
    let afCounter = 0;

    document
      .querySelectorAll("[data-af-id]")
      .forEach((el) => el.removeAttribute("data-af-id"));
    document.querySelectorAll("[data-af-group]").forEach((el) => {
      el.removeAttribute("data-af-group");
      el.removeAttribute("data-af-option-label");
    });

    const allInputs = Array.from(
      document.querySelectorAll("input, select, textarea")
    );

    function labelFor(el) {
      if (el.labels && el.labels.length) return el.labels[0].innerText.trim();
      if (el.closest("label")) return el.closest("label").innerText.trim();
      if (el.getAttribute("aria-label"))
        return el.getAttribute("aria-label").trim();
      if (el.placeholder) return el.placeholder.trim();
      if (el.id) {
        const lab = document.querySelector(`label[for="${el.id}"]`);
        if (lab) return lab.innerText.trim();
      }
      return "";
    }

    function isGenericLabel(text) {
      const t = text.trim().toLowerCase();
      if (t.length <= 3) return true;
      return /^(attach|browse|upload|choose file|select file|select|add|click here|file)$/.test(
        t
      );
    }

    // Custom-styled file-upload widgets very commonly hide the native
    // input and place a styled trigger button as its sibling under a
    // shared wrapper, with no <label>, aria-label, or for= connecting the
    // two (confirmed on RBC's careers site: input and its "Upload resume"
    // button are both direct children of the same wrapper div). Checked
    // only for file inputs; text/select fields already resolve correctly
    // through labelFor()/groupQuestionLabel() and don't need this.
    function nearbyButtonLabel(el) {
      let node = el.parentElement;
      for (let hops = 0; node && hops < 3; hops++) {
        const button = node.querySelector('button, a[role="button"], [role="button"]');
        const text = button ? (button.innerText || button.textContent || "").trim() : "";
        if (text && text.length > 2 && text.length < 100) return text;
        node = node.parentElement;
      }
      return "";
    }

    function groupQuestionLabel(el) {
      const fieldset = el.closest("fieldset");
      if (fieldset) {
        const legend = fieldset.querySelector("legend");
        if (legend && legend.innerText.trim()) return legend.innerText.trim();
      }

      let node = el.closest("div, section, form, tr") || el.parentElement;
      for (let hops = 0; node && hops < 8; hops++) {
        let sib = node.previousElementSibling;
        while (sib) {
          const text = (sib.innerText || "").trim();
          if (text && text.length > 2 && text.length < 400) return text;
          sib = sib.previousElementSibling;
        }
        node = node.parentElement;
      }
      return "";
    }

    allInputs.forEach((el) => {
      if (el.type === "hidden") return;
      // Account-credential fields (a candidate portal login/password, not
      // a screening question) are never reported as fillable at all, so
      // no future pattern addition can ever cause this to auto-fill a
      // password field.
      if (el.type === "password") return;

      const style = window.getComputedStyle(el);
      const isCustomWidget = el.type === "checkbox" || el.type === "radio";
      const exempt = el.tagName === "SELECT" || el.type === "file" || isCustomWidget;
      if (style.display === "none" && !exempt) return;
      if (style.visibility === "hidden" && !exempt) return;

      if (el.type === "checkbox" || el.type === "radio") {
        // Some Workday-built forms give checkboxes no `name` attribute at
        // all; everything is keyed by data-automation-id instead.
        // Grouping keyed purely on `name` would then scan every option in
        // an EEO race checklist ("Asian (Not Hispanic or Latino)", "Black
        // or African American", ...) as its own independent required
        // question instead of one group, since there is no shared `name`
        // to key on. Falls back to the shared question label above the
        // checkboxes (already computed by groupQuestionLabel for exactly
        // this purpose) as the grouping key when `name` is absent,
        // generic to any site built this way.
        const sharedLabel = el.name ? null : groupQuestionLabel(el);
        if (el.name || sharedLabel) {
          const groupKey = el.name
            ? el.type + ":name:" + el.name
            : el.type + ":label:" + sharedLabel;
          if (seenGroups.has(groupKey)) return;
          seenGroups.add(groupKey);

          const group = el.name
            ? allInputs.filter((other) => other.type === el.type && other.name === el.name)
            : allInputs.filter(
                (other) =>
                  other.type === el.type && !other.name && groupQuestionLabel(other) === sharedLabel
              );

          // A shared-label group of exactly one is just a regular single
          // checkbox that happens to sit under some nearby heading text,
          // not a real multi-option group. Only name-keyed groups (which
          // are unambiguous by construction) skip this check.
          if (!el.name && group.length < 2) {
            if (el.checked) return;
            let label = labelFor(el);
            if (!label || isGenericLabel(label)) label = sharedLabel || label;
            if (!label) return;
            const afId = afCounter++;
            el.setAttribute("data-af-id", String(afId));
            out.push({
              id: afId,
              kind: "single-checkbox",
              label: label.slice(0, 200),
              tag: el.tagName,
              required: !!(el.required || el.getAttribute("aria-required") === "true"),
              options: null,
            });
            return;
          }

          if (el.type === "radio" && group.some((g) => g.checked)) return;
          if (el.type === "checkbox" && group.every((g) => g.checked)) return;

          const options = group.map((g) => labelFor(g)).filter((t) => t);
          if (!options.length) return;

          // Tag every member with the same group id, plus its own option
          // text. A group has many elements, not one, so this uses its own
          // data-af-group attribute rather than data-af-id, letting
          // setGroupOption() later find and check whichever option's label
          // matches the chosen answer.
          const afId = afCounter++;
          group.forEach((member) => {
            member.setAttribute("data-af-group", String(afId));
            member.setAttribute("data-af-option-label", (labelFor(member) || "").slice(0, 200));
          });

          out.push({
            id: afId,
            kind: el.type === "radio" ? "radio-group" : "checkbox-group",
            label: (groupQuestionLabel(el) || options[0]).slice(0, 200),
            tag: "GROUP",
            required: !!(el.required || el.getAttribute("aria-required") === "true"),
            options: options,
          });
          return;
        }

        if (el.checked) return;
        let label = labelFor(el);
        if (!label || isGenericLabel(label)) label = groupQuestionLabel(el) || label;
        if (!label) return;

        const afId = afCounter++;
        el.setAttribute("data-af-id", String(afId));

        out.push({
          id: afId,
          kind: "single-checkbox",
          label: label.slice(0, 200),
          tag: el.tagName,
          required: !!(el.required || el.getAttribute("aria-required") === "true"),
          options: null,
        });
        return;
      }

      if (el.tagName === "SELECT") {
        if (el.value && el.value !== "") return;
      } else if (el.value && el.value.trim() !== "") {
        return;
      }

      let label = labelFor(el);
      if ((!label || isGenericLabel(label)) && el.type === "file") {
        label = nearbyButtonLabel(el) || label;
      }
      if (!label || isGenericLabel(label)) label = groupQuestionLabel(el) || label;
      if (!label) return;

      let options = null;
      if (el.tagName === "SELECT") {
        options = Array.from(el.options)
          .map((o) => o.text.trim())
          .filter((t) => t && !/^select/i.test(t));
      }

      let kind = "text";
      if (el.tagName === "SELECT") kind = "select";
      else if (el.type === "file") kind = "file";

      const afId = afCounter++;
      el.setAttribute("data-af-id", String(afId));

      out.push({
        id: afId,
        kind: kind,
        label: label.slice(0, 200),
        tag: el.tagName,
        required: !!(el.required || el.getAttribute("aria-required") === "true"),
        options: options,
      });
    });

    return out;
  }

  function getByAfId(afId) {
    return document.querySelector(`[data-af-id="${afId}"]`);
  }

  return {
    setNativeValue,
    setSelectByText,
    setCheckbox,
    setGroupOption,
    lowerXPath,
    lowerXPathOwnText,
    clickableXPath,
    firstXPathMatch,
    isVisible,
    queryLabelLikeElements,
    fieldForLabelLikeElement,
    hasIdentityFields,
    clickAdvanceButton,
    discoverDropdownOptions,
    scanIncompleteFields,
    getByAfId,
  };
})();
