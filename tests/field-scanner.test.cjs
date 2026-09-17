// Run: npm test  (or: node --test tests/)
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const SCANNER_SOURCE = fs.readFileSync(
  path.join(__dirname, '..', 'extension', 'field_scanner.js'),
  'utf8'
);

function loadScanner(bodyHtml) {
  const html = `<!doctype html><html><body>${bodyHtml}</body></html>`;
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://example.com/apply' });

  // jsdom has no layout engine, so it never implements innerText (a
  // rendered-text property, unlike textContent). field_scanner.js relies
  // on innerText in the real browser deliberately, since it excludes
  // hidden/display:none descendant text where textContent would not; this
  // polyfill is test-only and loses that distinction, which none of these
  // structural tests depend on.
  Object.defineProperty(dom.window.HTMLElement.prototype, 'innerText', {
    configurable: true,
    get() {
      return this.textContent;
    },
  });

  dom.window.eval(SCANNER_SOURCE);
  return dom.window;
}

function fileFields(fields) {
  return fields.filter((f) => f.kind === 'file');
}

test('a real <label for> still resolves normally', () => {
  const window = loadScanner(`
    <label for="firstName">First Name</label>
    <input id="firstName">
  `);
  const fields = window.AutofillScanner.scanIncompleteFields();
  assert.equal(fields.length, 1);
  assert.equal(fields[0].label, 'First Name');
  assert.equal(fields[0].kind, 'text');
});

// Regression case: RBC's careers site (jobs.rbc.com) hides the native file
// input and renders a styled "Upload resume" button as its sibling under a
// shared wrapper div, with no <label>, aria-label, or for= connecting the
// two. Before nearbyButtonLabel() existed, this file input's label fell
// through to the page's step-navigation breadcrumb text ("My information,
// My experience, ..."), so the automation service's resume-upload matching
// (which keys off the label containing "resume") never fired.
test('a file input labeled only by a sibling button resolves to the button text', () => {
  const window = loadScanner(`
    <nav>My information My experience Application questions Review</nav>
    <div class="resume-section">
      <div class="resume-upload-wrapper">
        <input type="file">
        <button class="upload-resume-btn">Upload resume</button>
      </div>
    </div>
  `);
  const fields = fileFields(window.AutofillScanner.scanIncompleteFields());
  assert.equal(fields.length, 1);
  assert.equal(fields[0].label, 'Upload resume');
});

test('a file input with a real label is not overridden by a nearby button', () => {
  const window = loadScanner(`
    <label for="coverLetter">Upload Cover Letter</label>
    <input id="coverLetter" type="file">
    <button>Browse</button>
  `);
  const fields = fileFields(window.AutofillScanner.scanIncompleteFields());
  assert.equal(fields.length, 1);
  assert.equal(fields[0].label, 'Upload Cover Letter');
});

test('a text field near an unrelated button still uses the broader fallback, not the button', () => {
  // groupQuestionLabel() walks up to the input's own wrapper div and reads
  // that wrapper's previous sibling, so the label and the field each need
  // their own row wrapper for the "previous sibling" text to be found.
  const window = loadScanner(`
    <div>
      <div>Preferred contact method</div>
      <div><input id="contactMethod"></div>
      <div><button>Save</button></div>
    </div>
  `);
  const fields = window.AutofillScanner.scanIncompleteFields();
  assert.equal(fields.length, 1);
  assert.equal(fields[0].label, 'Preferred contact method');
});

test('a required checkbox group is reported with its options', () => {
  const window = loadScanner(`
    <fieldset>
      <legend>Willing to relocate?</legend>
      <label><input type="radio" name="relocate" value="yes"> Yes</label>
      <label><input type="radio" name="relocate" value="no"> No</label>
    </fieldset>
  `);
  const fields = window.AutofillScanner.scanIncompleteFields();
  assert.equal(fields.length, 1);
  assert.equal(fields[0].kind, 'radio-group');
  assert.equal(fields[0].label, 'Willing to relocate?');
  // Array.from copies into a same-realm array; the scanner's array is a
  // jsdom-realm object, and deepEqual's strict prototype check fails
  // across realms even when the values are identical.
  assert.deepEqual(Array.from(fields[0].options), ['Yes', 'No']);
});

// Regression: setNativeValue() only ever touched .value, so a checkbox
// field (like "I currently work here" or "This is my native language.")
// was silently a no-op under the old write path -- it never actually got
// checked, but nothing reported that failure either.
test('setCheckbox actually checks/unchecks based on the answer', () => {
  const window = loadScanner('<input type="checkbox" id="native">');
  const el = window.document.getElementById('native');

  assert.equal(window.AutofillScanner.setCheckbox(el, 'Yes'), true);
  assert.equal(el.checked, true);

  assert.equal(window.AutofillScanner.setCheckbox(el, 'No'), true);
  assert.equal(el.checked, false);
});

// Regression: a radio-group/checkbox-group field had no data-af-id at
// all, so getByAfId() could never find it again to write an answer --
// group fields were detected but silently unfillable. setGroupOption()
// uses the group id scanIncompleteFields() now tags each member with.
test('a radio group can be answered by its option label after scanning', () => {
  const window = loadScanner(`
    <fieldset>
      <legend>Willing to relocate?</legend>
      <label><input type="radio" name="relocate" value="yes"> Yes</label>
      <label><input type="radio" name="relocate" value="no"> No</label>
    </fieldset>
  `);
  const [field] = window.AutofillScanner.scanIncompleteFields();

  assert.equal(window.AutofillScanner.setGroupOption(field.id, 'No'), true);
  const noRadio = window.document.querySelector('input[value="no"]');
  assert.equal(noRadio.checked, true);
});

test('setGroupOption returns false when no option label matches', () => {
  const window = loadScanner(`
    <fieldset>
      <legend>Willing to relocate?</legend>
      <label><input type="radio" name="relocate" value="yes"> Yes</label>
      <label><input type="radio" name="relocate" value="no"> No</label>
    </fieldset>
  `);
  const [field] = window.AutofillScanner.scanIncompleteFields();

  assert.equal(window.AutofillScanner.setGroupOption(field.id, 'Maybe'), false);
});


test('file answers cannot abort other field writes', () => {
  const window = loadScanner(`
    <label for="first">First Name</label><input id="first">
    <label for="resume">Resume/CV</label><input id="resume" type="file">
    <label for="last">Last Name</label><input id="last">
  `);
  window.eval(fs.readFileSync(path.join(__dirname, '..', 'extension', 'content.js'), 'utf8'));
  const fields = window.AutofillScanner.scanIncompleteFields();
  const answers = fields.map(f=>({id:f.id,kind:f.kind,value:'Demo'}));
  const filled = window.__runJobApplyBotApplyAnswers(answers);
  assert.equal(filled.length, 2);
  assert.equal(window.document.getElementById('first').value, 'Demo');
  assert.equal(window.document.getElementById('last').value, 'Demo');
  assert.equal(window.document.getElementById('resume').files.length, 0);
});
