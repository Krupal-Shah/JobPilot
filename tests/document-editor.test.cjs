const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '..');
const stages = ['bookmarked', 'applied', 'interview', 'offer', 'rejected'];

function editor() {
  const columns = stages.map(stage => `<div id="column-${stage}"></div><span id="count-${stage}"></span>`).join('');
  const html = fs.readFileSync(path.join(root, 'templates/dashboard.html'), 'utf8');
  const dom = new JSDOM(html + columns, { url: 'http://localhost/', runScripts: 'outside-only' });
  const { window } = dom;
  const app = { id: 1, company: 'Example', title: 'Engineer', stage: 'bookmarked',
    url: 'https://example.com/job', description: 'Python Docker' };
  const state = {
    structure: { experience: [{ id: 'experience_1', bullets: ['experience_1_bullet_1'] }],
      projects: [], leadership: [] },
    values: { experience_1_bullet_1: 'Built an API.' },
    visible: { experience: ['experience:0'], projects: [], leadership: [] },
  };
  const draft = { state, version: 'v1', pdf_url: '/resume.pdf', selection_method: 'keyword',
    score: { percent: 50, feedback: 'One skill matches.', missing_skills: ['docker'] },
    entries: [{ id: 'experience:0', category: 'experience', title: 'Engineer at Example',
      visible: true, bullets: [{ id: 'experience_1_bullet_1', text: 'Built an API.' }] }] };
  const writes = [];
  const previews = [];
  window.URL.createObjectURL = () => 'blob:pdf';
  window.URL.revokeObjectURL = () => {};
  window.fetch = async (url, options = {}) => {
    if (url.startsWith('/resume.pdf')) return { ok: true, headers: { get: () => 'application/pdf' },
      blob: async () => new window.Blob(['pdf']) };
    if (url.endsWith('/documents/resume/preview')) {
      previews.push(JSON.parse(options.body));
      return { ok: true, blob: async () => new window.Blob(['live pdf']) };
    }
    if (options.method === 'PUT') {
      writes.push(JSON.parse(options.body));
      return { ok: true, json: async () => ({ version: 'v2', score: draft.score }) };
    }
    if (url.endsWith('/suggest')) return { ok: true, json: async () => ({ proposal: 'Hello manager' }) };
    if (url.endsWith('/documents/resume')) return { ok: true, json: async () => structuredClone(draft) };
    if (url.endsWith('/documents/cover-letter')) return { ok: true,
      json: async () => ({ text: 'Dear Hiring Manager,', saved: false }) };
    if (url.endsWith('/documents/cold-email')) return { ok: true,
      json: async () => ({ text: 'Hi [Hiring Manager Name],' }) };
    return { ok: true, json: async () => url.includes('automation') ? { running: false } : [app] };
  };
  window.eval(fs.readFileSync(path.join(root, 'static/js/dashboard.js'), 'utf8'));
  window.eval(fs.readFileSync(path.join(root, 'static/js/document-editor.js'), 'utf8'));
  return { dom, window, writes, previews };
}

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

test('card opens a side-by-side resume editor and saves point edits', async () => {
  const { dom, window, writes } = editor();
  await tick();
  const buttons = [...window.document.querySelectorAll('.kanban-doc-actions button')];
  assert.deepEqual(buttons.map(button => button.textContent),
    ['Tailor resume', 'Cover letter', 'Cold email']);
  assert.equal(window.document.querySelector('.kanban-stage-select'), null);
  buttons[0].click();
  await tick();
  const modal = window.document.getElementById('documentModal');
  assert.equal(modal.hidden, false);
  assert.equal(window.document.querySelectorAll('#documentEntries .document-entry').length, 1);
  const point = window.document.querySelector('#documentEntries [data-point-id="experience_1_bullet_1"]');
  point.value = 'Built a Python API.';
  point.dispatchEvent(new window.Event('input', { bubbles: true }));
  window.document.getElementById('resumeSave').click();
  await tick();
  assert.equal(writes[0].state.values.experience_1_bullet_1, 'Built a Python API.');
  assert.equal(writes[0].version, 'v1');
  dom.window.close();
});

test('point edits refresh the PDF before saving', async () => {
  const { dom, window, writes, previews } = editor();
  await tick();
  window.document.querySelector('.kanban-doc-actions button').click();
  await tick();
  const point = window.document.querySelector('#documentEntries [data-point-id="experience_1_bullet_1"]');
  point.value = 'Built a reviewed API.';
  point.dispatchEvent(new window.Event('input', { bubbles: true }));
  await new Promise(resolve => setTimeout(resolve, 600));
  assert.equal(previews.length, 1);
  assert.equal(previews[0].state.values.experience_1_bullet_1, 'Built a reviewed API.');
  assert.equal(writes.length, 0);
  assert.match(window.document.getElementById('documentPdf').src, /toolbar=0.*zoom=page-width/);
  dom.window.close();
});

test('cover letter can be saved, while cold email has only copy', async () => {
  const { dom, window, writes } = editor();
  await tick();
  const buttons = [...window.document.querySelectorAll('.kanban-doc-actions button')];
  buttons[1].click();
  await tick();
  assert.equal(window.document.getElementById('documentSave').hidden, false);
  const letter = window.document.getElementById('documentText');
  letter.setSelectionRange(0, 4);
  window.document.getElementById('documentAskSelection').click();
  await tick();
  window.document.querySelector('#textSuggestions .dashboard-primary').click();
  assert.equal(letter.value, 'Hello manager Hiring Manager,');
  window.document.getElementById('documentText').value = 'Revised letter';
  window.document.getElementById('documentSave').click();
  await tick();
  assert.equal(writes[0].text, 'Revised letter');
  window.document.getElementById('documentClose').click();
  buttons[2].click();
  await tick();
  assert.equal(window.document.getElementById('documentSave').hidden, true);
  assert.equal(window.document.getElementById('documentCopy').hidden, false);
  dom.window.close();
});
