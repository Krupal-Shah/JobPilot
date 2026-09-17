// Run: npm test  (or: node --test tests/)
//
// Covers window.__runJobApplyBotCheckSubmission(): the generic, non-site-
// specific signal background.js polls for after a human has manually
// clicked the real ATS's own Submit button, so it knows when to update
// the tracker and offer the next bookmarked job. This never clicks or
// submits anything itself; see extension/content.js for that boundary.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const SCANNER_SOURCE = fs.readFileSync(path.join(__dirname, '..', 'extension', 'field_scanner.js'), 'utf8');
const CONTENT_SOURCE = fs.readFileSync(path.join(__dirname, '..', 'extension', 'content.js'), 'utf8');

function loadPage(bodyHtml, url) {
  const html = `<!doctype html><html><body>${bodyHtml}</body></html>`;
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: url || 'https://example.com/apply' });

  // See tests/field-scanner.test.cjs for why this polyfill exists and
  // what it does not cover (jsdom has no layout engine).
  Object.defineProperty(dom.window.HTMLElement.prototype, 'innerText', {
    configurable: true,
    get() {
      return this.textContent;
    },
  });

  // jsdom has no layout engine: every element's real getBoundingClientRect()
  // is zeroed, which would make field_scanner.js's isVisible() (and so
  // hasIdentityFields()) report everything as invisible regardless of CSS.
  // A fixed non-zero rect restores "does CSS hide this" as the only signal,
  // same as it fixed the innerText gap above.
  dom.window.Element.prototype.getBoundingClientRect = function () {
    return { width: 100, height: 20, top: 0, left: 0, bottom: 20, right: 100 };
  };

  dom.window.eval(SCANNER_SOURCE);
  dom.window.eval(CONTENT_SOURCE);
  return dom.window;
}

test('confirmation text on the page is detected regardless of URL', () => {
  const window = loadPage('<h1>Thank you for your application!</h1>', 'https://boards.greenhouse.io/blah/jobs/1');
  assert.equal(window.__runJobApplyBotCheckSubmission(), true);
});

test('a confirmation-shaped URL with the form gone is detected', () => {
  const window = loadPage('<p>You can close this window.</p>', 'https://jobs.lever.co/blah/apply/confirmation');
  assert.equal(window.__runJobApplyBotCheckSubmission(), true);
});

test('a confirmation-shaped URL is NOT enough while the form is still on screen', () => {
  const window = loadPage(
    '<label for="fn">First Name</label><input id="fn" name="first_name">',
    'https://jobs.lever.co/blah/apply/confirmation'
  );
  assert.equal(window.__runJobApplyBotCheckSubmission(), false);
});

test('an ordinary application page is not a false positive', () => {
  const window = loadPage(
    '<label for="fn">First Name</label><input id="fn" name="first_name"><p>Submit your application below.</p>',
    'https://boards.greenhouse.io/blah/jobs/1'
  );
  assert.equal(window.__runJobApplyBotCheckSubmission(), false);
});

test('an unrelated page with neither signal is not a false positive', () => {
  const window = loadPage('<h1>Careers at Blah Blah Inc.</h1>', 'https://www.blahblah.example/careers');
  assert.equal(window.__runJobApplyBotCheckSubmission(), false);
});


test('visible Apply button is found after hidden and disabled copies', () => {
  const window = loadPage('<button style="display:none">Apply now</button><button disabled>Apply now</button><a id="apply" href="/form">Apply now</a>');
  let clicked = false;
  window.document.getElementById('apply').addEventListener('click', event => {
    event.preventDefault();
    clicked = true;
  });
  assert.equal(window.__runJobApplyBotPageState().applyButtonText, 'Apply now');
  assert.equal(window.__runJobApplyBotStartApplication().clicked, true);
  assert.equal(clicked, true);
  window.close();
});
