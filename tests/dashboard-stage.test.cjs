const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '..');
const stages = ['bookmarked', 'applied', 'interview', 'offer', 'rejected'];

function board(fetchResponse) {
  const columns = stages.map(stage => `<div id="column-${stage}"></div><span id="count-${stage}"></span>`).join('');
  const html = fs.readFileSync(path.join(root, 'templates/dashboard.html'), 'utf8');
  const dom = new JSDOM(html + columns, { url: 'http://localhost/', runScripts: 'outside-only' });
  const { window } = dom;
  const applications = [{ id: 1, title: 'Engineer', company: 'Example', stage: 'bookmarked', url: 'https://example.com', description: 'Requirements:\n- Build things' }];
  window.fetch = (url, options) => options?.method === 'PATCH' ? fetchResponse(url, options) : Promise.resolve({ ok: true, json: async () => url.includes('automation') ? { running: false } : applications });
  window.eval(fs.readFileSync(path.join(root, 'static/js/dashboard.js'), 'utf8'));

  return dom;
}

test('moving a bookmarked card updates both columns immediately', async () => {
  const dom = board(() => new Promise(() => {}));
  const { window } = dom;
  await new Promise(resolve => setTimeout(resolve, 0));
  const card = window.document.querySelector('#column-bookmarked .kanban-card');
  window.eval("updateStage(1, 'applied', document.querySelector('#column-bookmarked .kanban-card'))");
  assert.equal(window.document.querySelector('#column-bookmarked .kanban-card'), null);
  assert.equal(window.document.querySelector('#column-applied .kanban-card'), card);
  assert.equal(window.document.getElementById('count-bookmarked').textContent, '0');
  assert.equal(window.document.getElementById('count-applied').textContent, '1');
  dom.window.close();
});

test('a failed stage save restores the bookmarked card', async () => {
  const dom = board(async () => ({ ok: false }));
  const { window } = dom;
  await new Promise(resolve => setTimeout(resolve, 0));
  await window.updateStage(1, 'interview', window.document.querySelector('#column-bookmarked .kanban-card'));
  assert.equal(window.document.querySelectorAll('#column-bookmarked .kanban-card').length, 1);
  assert.equal(window.document.querySelectorAll('#column-interview .kanban-card').length, 0);
  assert.match(window.document.getElementById('boardStatus').textContent, /Could not move/);
  dom.window.close();
});

test('job description separates headings and bullets as text', async () => {
  const dom = board(() => new Promise(() => {}));
  const { window } = dom;
  await new Promise(resolve => setTimeout(resolve, 0));
  window.document.querySelector('#column-bookmarked .kanban-card').click();
  const description = window.document.getElementById('modalDescription');
  assert.equal(description.querySelector('h3').textContent, 'Requirements');
  assert.equal(description.querySelector('li').textContent, 'Build things');
  window.renderDescription('Overview:\n<script>alert(1)</script>', description);
  assert.equal(description.querySelector('script'), null);
  assert.match(description.textContent, /<script>/);
  dom.window.close();
});
