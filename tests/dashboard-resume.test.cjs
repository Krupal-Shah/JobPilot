const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'templates/dashboard.html'), 'utf8');
const columns = ['bookmarked', 'applied', 'interview', 'offer', 'rejected']
  .map(stage => `<div id="column-${stage}"></div><span id="count-${stage}"></span>`).join('');
const dom = new JSDOM(template + columns, { url: 'http://localhost/', runScripts: 'outside-only' });
const { window } = dom;
window.fetch = () => new Promise(() => {});
window.eval(fs.readFileSync(path.join(root, 'static/js/dashboard.js'), 'utf8'));
const application = {
  id: 1, title: 'Engineer', company: 'Example', stage: 'bookmarked',
  url: 'https://example.com/jobs/1', resume_pdf_url: '/api/extension/resume?url=https%3A%2F%2Fexample.com%2Fjobs%2F1',
};
const card = window.buildCard(application);
const buttons = [...card.querySelectorAll('.kanban-doc-actions button')];
assert.deepEqual(buttons.map(button => button.textContent),
  ['Tailor resume', 'Cover letter', 'Cold email']);
window.document.body.appendChild(card);
const modal = window.document.getElementById('expandModal');
modal.hidden = true;
window.openModal(application);
const modalLink = window.document.getElementById('modalViewResume');
assert.equal(modalLink.hidden, false);
assert.equal(modalLink.getAttribute('href'), application.resume_pdf_url);
const withoutResume = { ...application, id: 2, resume_pdf_url: null };
window.openModal(withoutResume);
assert.equal(modalLink.hidden, true);
assert.equal(modalLink.hasAttribute('href'), false, 'Another application must not retain the previous resume link');
assert.match(window.document.getElementById('modalResumeStatus').textContent, /Open Tailor resume/);
assert.equal(window.buildCard(withoutResume).querySelectorAll('.kanban-doc-actions button').length, 3);
window.close();
