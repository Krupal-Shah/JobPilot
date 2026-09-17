// Run: npm test  (or: node --test tests/)
//
// One test per real HTML control type our autofill has to handle: plain
// text, textarea, native <select>, a lone checkbox, and a radio group.
// Runs the exact same two-step pipeline production code uses (scan the
// page, then apply answers back through content.js, not field_scanner.js
// directly) so this exercises the real write path, not just the scanner.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const SCANNER_SOURCE = fs.readFileSync(path.join(__dirname, '..', 'extension', 'field_scanner.js'), 'utf8');
const CONTENT_SOURCE = fs.readFileSync(path.join(__dirname, '..', 'extension', 'content.js'), 'utf8');

function loadPage(bodyHtml) {
  const html = `<!doctype html><html><body>${bodyHtml}</body></html>`;
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://example.com/apply' });

  // See field-scanner.test.cjs for why this polyfill exists and what it
  // does not cover.
  Object.defineProperty(dom.window.HTMLElement.prototype, 'innerText', {
    configurable: true,
    get() {
      return this.textContent;
    },
  });

  dom.window.eval(SCANNER_SOURCE);
  dom.window.eval(CONTENT_SOURCE);
  return dom.window;
}

// Mirrors background.js's runFillOnTab(): scan, then apply an answer per
// field by label, then report which ids actually got written.
function fillByLabel(window, answersByLabel) {
  const scan = window.__runJobApplyBotScan();
  const answers = scan.fields
    .filter((f) => f.label in answersByLabel)
    .map((f) => ({ id: f.id, kind: f.kind, value: answersByLabel[f.label] }));
  const successfulIds = window.__runJobApplyBotApplyAnswers(answers);
  return { scan, successfulIds };
}

test('plain text input gets filled', () => {
  const window = loadPage('<label for="fullName">Full Name</label><input id="fullName">');
  const { successfulIds } = fillByLabel(window, { 'Full Name': 'Blah Blah' });

  assert.equal(successfulIds.length, 1);
  assert.equal(window.document.getElementById('fullName').value, 'Blah Blah');
});

test('textarea gets filled the same way as a text input', () => {
  const window = loadPage('<label for="bio">Bio</label><textarea id="bio"></textarea>');
  const { scan, successfulIds } = fillByLabel(window, { Bio: 'Ships code.' });

  assert.equal(scan.fields[0].kind, 'text');
  assert.equal(successfulIds.length, 1);
  assert.equal(window.document.getElementById('bio').value, 'Ships code.');
});

test('native select gets set when the answer matches an option', () => {
  const window = loadPage(`
    <label for="country">Country</label>
    <select id="country">
      <option value="">Please Select</option>
      <option value="ca">Canada</option>
      <option value="us">United States</option>
    </select>
  `);
  const { successfulIds } = fillByLabel(window, { Country: 'Canada' });

  assert.equal(successfulIds.length, 1);
  assert.equal(window.document.getElementById('country').value, 'ca');
});

// Regression: this used to be reported as "filled" even when nothing
// changed, because the write path never checked whether setSelectByText
// actually found a matching option.
test('native select is reported as NOT filled when no option matches', () => {
  const window = loadPage(`
    <label for="degree">Degree</label>
    <select id="degree">
      <option value="">Please Select</option>
      <option value="ba">Bachelor of Arts</option>
    </select>
  `);
  const { successfulIds } = fillByLabel(window, { Degree: "Bachelor's Degree" });

  assert.equal(successfulIds.length, 0);
  assert.equal(window.document.getElementById('degree').value, '');
});

test('a lone checkbox is checked, not just given a text value', () => {
  const window = loadPage('<label><input type="checkbox" id="native"> This is my native language.</label>');
  const { scan, successfulIds } = fillByLabel(window, { 'This is my native language.': 'Yes' });

  assert.equal(scan.fields[0].kind, 'single-checkbox');
  assert.equal(successfulIds.length, 1);
  assert.equal(window.document.getElementById('native').checked, true);
});

test('a radio group is answered by checking the matching option', () => {
  const window = loadPage(`
    <fieldset>
      <legend>Willing to relocate?</legend>
      <label><input type="radio" name="relocate" value="yes"> Yes</label>
      <label><input type="radio" name="relocate" value="no"> No</label>
    </fieldset>
  `);
  const { scan, successfulIds } = fillByLabel(window, { 'Willing to relocate?': 'No' });

  assert.equal(scan.fields[0].kind, 'radio-group');
  assert.equal(successfulIds.length, 1);
  assert.equal(window.document.querySelector('input[value="no"]').checked, true);
  assert.equal(window.document.querySelector('input[value="yes"]').checked, false);
});

test('a file input is left for the extension-specific upload path, not text/select writing', () => {
  const window = loadPage('<label for="resume">Upload Resume</label><input id="resume" type="file">');
  const scan = window.__runJobApplyBotScan();

  assert.equal(scan.fields[0].kind, 'file');
  // File uploads use attachResumeFile in the page MAIN world; background-worker
  // tests cover routing and failure reporting.
  // rather than __runJobApplyBotApplyAnswers, which only handles text
  // values, select options, and checkbox/group state.
});

test('one page with every control type: only the ones with real matching answers succeed', () => {
  const window = loadPage(`
    <label for="fullName">Full Name</label><input id="fullName">
    <label for="bio">Bio</label><textarea id="bio"></textarea>
    <label for="country">Country</label>
    <select id="country">
      <option value="">Please Select</option>
      <option value="ca">Canada</option>
    </select>
    <label><input type="checkbox" id="native"> This is my native language.</label>
    <fieldset>
      <legend>Willing to relocate?</legend>
      <label><input type="radio" name="relocate" value="yes"> Yes</label>
      <label><input type="radio" name="relocate" value="no"> No</label>
    </fieldset>
    <label for="resume">Upload Resume</label><input id="resume" type="file">
  `);

  const { scan, successfulIds } = fillByLabel(window, {
    'Full Name': 'Blah Blah',
    Bio: 'Ships code.',
    Country: 'Canada',
    'This is my native language.': 'Yes',
    'Willing to relocate?': 'No',
    // No answer given for the file input here on purpose -- confirms the
    // other five types succeed independently of it.
  });

  assert.equal(scan.fields.length, 6);
  assert.equal(successfulIds.length, 5);
  assert.equal(window.document.getElementById('fullName').value, 'Blah Blah');
  assert.equal(window.document.getElementById('bio').value, 'Ships code.');
  assert.equal(window.document.getElementById('country').value, 'ca');
  assert.equal(window.document.getElementById('native').checked, true);
  assert.equal(window.document.querySelector('input[value="no"]').checked, true);
});

// Safety: a candidate-portal login password should never be reported as
// fillable at all, so no future field_map/screening_answers pattern can
// ever cause it to be auto-filled.
test('a password field is never reported as a fillable field', () => {
  const window = loadPage('<label for="pw">Password</label><input id="pw" type="password">');
  const scan = window.__runJobApplyBotScan();

  assert.equal(scan.fields.length, 0);
});

// Regression: real labels are inconsistent about whether there's a space
// before a required-field asterisk ("Legal First Name*" vs "First Name
// *"), and some prepend extra words ("Legal First Name" vs "First
// Name"). An over-specific anchored pattern matched one shape and missed
// the other; confirmed on iCIMS's "Legal First Name*".
test('name fields match regardless of a prefix word or asterisk spacing', () => {
  const window = loadPage(`
    <label for="firstName">Legal First Name*</label><input id="firstName">
    <label for="lastName">Last Name *</label><input id="lastName">
  `);
  const scan = window.__runJobApplyBotScan();

  assert.equal(scan.fields.length, 2);
  assert.equal(scan.fields[0].label, 'Legal First Name*');
  assert.equal(scan.fields[1].label, 'Last Name *');
});
