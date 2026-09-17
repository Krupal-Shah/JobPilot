const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

function worker(failUpdate) {
  const stored = {
    started_1: true,
    jobCache_1: { url: 'https://example.com/job' },
  };
  let banners = 0;
  const listener = { addListener() {} };
  const context = vm.createContext({
    self: { addEventListener() {} },
    chrome: {
      tabs: { onRemoved: listener, onUpdated: listener },
      runtime: { onMessage: listener },
      storage: { local: {
        async get(key) { return { [key]: stored[key] }; },
        async set(values) { Object.assign(stored, values); },
      } },
      scripting: { async executeScript(options) {
        if (options.files) return [];
        if (options.func.name === 'injectSubmissionBanner') { banners++; return []; }
        return [{ result: true }];
      } },
    },
    async fetch(url, options = {}) {
      if (url.endsWith('/log')) return {};
      if (options.method === 'PATCH') {
        return { ok: !failUpdate, status: failUpdate ? 500 : 200,
          async text() { return JSON.stringify(failUpdate ? {error: 'Failed'} : {stage: 'applied'}); } };
      }
      return { ok: true, async text() {
        return JSON.stringify([{ id: 1, url: 'https://example.com/job', stage: 'bookmarked' }]);
      } };
    },
  });
  vm.runInContext(fs.readFileSync('extension/background.js', 'utf8'), context);
  return { context, stored, banners: () => banners };
}

test('failed submission write stays retryable and never shows success', async () => {
  const w = worker(true);
  await w.context.checkForSubmissionConfirmation({id: 1, url: 'https://example.com/thanks'});
  assert.equal(w.stored.submitted_1, undefined);
  assert.equal(w.banners(), 0);
});

test('successful submission write is recorded and shown once', async () => {
  const w = worker(false);
  const tab = {id: 1, url: 'https://example.com/thanks'};
  await w.context.checkForSubmissionConfirmation(tab);
  await w.context.checkForSubmissionConfirmation(tab);
  assert.equal(w.stored.submitted_1, true);
  assert.equal(w.banners(), 1);
});

function resumeWorker() {
  const w = worker(false);
  w.stored.jobCache_1.description = 'Build Python applications';
  w.stored.jobCache_1.title = 'Developer';
  w.context.btoa = (value) => Buffer.from(value, 'binary').toString('base64');
  w.uploads = [];
  w.context.chrome.scripting.executeScript = async (options) => {
    if (options.world !== 'MAIN') return [{result: options.args[0]}];
    assert.equal(options.func.name, 'attachResumeFile');
    w.uploads.push(options.args);
    return [{result: true}];
  };
  w.upload = () => w.context.uploadResumeToUnmappedFileFields(
    {id: 1, url: 'https://example.com/application'},
    {fields: [{id: 'resume', kind: 'file', label: 'Resume'}]}, 0, ['Resume']);
  return w;
}

function pdfResponse() {
  return {ok: true, status: 200,
    headers: {get: () => 'application/pdf'},
    async arrayBuffer() { return Uint8Array.from([37, 80, 68, 70]).buffer; }};
}

test('every resume upload receives freshly tailored PDF directly', async () => {
  const w = resumeWorker();
  const requests = [];
  w.context.fetch = async (url, options) => {
    requests.push({url, options});
    return pdfResponse();
  };
  await w.upload();
  await w.upload();
  assert.equal(requests.length, 2);
  for (const offset of [0, 1]) {
    assert.match(requests[offset].url, /tailor-resume$/);
    const payload = JSON.parse(requests[offset].options.body);
    assert.equal(payload.text, 'Build Python applications');
    assert.equal(payload.url, 'https://example.com/job');
    assert.equal(requests[offset].options.headers.Accept, 'application/pdf');
  }
  assert.equal(w.uploads.length, 2);
  assert.equal(w.uploads[0][2], 'tailored_resume.pdf');
});

test('master API response uploads with the master filename', async () => {
  const w = resumeWorker();
  w.context.fetch = async () => ({
    ...pdfResponse(),
    headers: {get: name => name === 'X-Resume-Type' ? 'master' : 'application/pdf'},
  });
  await w.upload();
  assert.equal(w.uploads.length, 1);
  assert.equal(w.uploads[0][2], 'master_resume.pdf');
  assert.equal(w.uploads[0][3], 'application/pdf');
});

test('saved cover letter is attached only to cover-letter file fields', async () => {
  const w = resumeWorker();
  w.context.fetch = async () => pdfResponse();
  const uploaded = await w.context.uploadCoverLetterToUnmappedFileFields(
    {id: 1}, {fields: [
      {id: 'cover', kind: 'file', label: 'Cover Letter'},
      {id: 'resume', kind: 'file', label: 'Resume'},
    ]}, 0, ['Cover Letter', 'Resume'], 12);
  assert.equal(uploaded.length, 1);
  assert.equal(uploaded[0], 'Cover Letter');
  assert.equal(w.uploads[0][2], 'cover_letter.pdf');
  assert.equal(w.uploads[0][3], 'application/pdf');
});

test('no saved cover letter leaves the file field for review', async () => {
  const w = resumeWorker();
  w.context.fetch = async () => ({status: 404, ok: false});
  const uploaded = await w.context.uploadCoverLetterToUnmappedFileFields(
    {id: 1}, {fields: [{id: 'cover', kind: 'file', label: 'Cover Letter'}]},
    0, ['Cover Letter'], 12);
  assert.equal(uploaded.length, 0);
  assert.equal(w.uploads.length, 0);
});

test('failed generation or login never uploads a fallback file', async () => {
  for (const code of [401, 422]) {
    const w = resumeWorker();
    let requests = 0;
    w.context.fetch = async () => {
      requests++;
      return {ok: false, status: code, async text() { return '{"error":"Setup required"}'; }};
    };
    await assert.rejects(w.upload(), /Setup required/);
    assert.equal(w.uploads.length, 0);
  }
});

test('missing captured description leaves resume field for review', async () => {
  const w = resumeWorker();
  delete w.stored.jobCache_1;
  w.context.fetch = async () => ({ok: true, async text() { return '[]'; }});
  await assert.rejects(w.upload(), /Open the job posting/);
  assert.equal(w.uploads.length, 0);
});


test('resume upload recovers a saved bookmark when tab cache is empty', async () => {
  const w = resumeWorker();
  delete w.stored.jobCache_1;
  const requests = [];
  w.context.fetch = async (url, options) => {
    requests.push(url);
    if (url.endsWith('/api/applications')) return {ok: true, async text() {
      return JSON.stringify([{url: 'https://example.com/application', description: 'Saved posting', title: 'Job'}]);
    }};
    return pdfResponse();
  };
  await w.upload();
  assert.equal(w.stored.jobCache_1.description, 'Saved posting');
  assert.match(requests[1], /tailor-resume$/);
  assert.equal(w.uploads.length, 1);
});

test('full fill excludes resume from text answers and calls tailoring then download', async () => {
  const w = resumeWorker();
  const requests = [];
  w.context.chrome.scripting.executeScript = async (options) => {
    if (options.files) return [];
    if (options.func.toString().includes('__runJobApplyBotScan')) return [{frameId: 0, result: {fields: [
      {id: 1, label: 'First Name', kind: 'text'}, {id: 2, label: 'Resume/CV*', kind: 'file'},
    ]}}];
    if (options.func.toString().includes('__runJobApplyBotApplyAnswers')) return [{result: [1]}];
    if (options.world !== 'MAIN') return [{result: options.args[0]}];
    assert.equal(options.func.name, 'attachResumeFile');
    w.uploads.push(options.args);
    return [{result: true}];
  };
  w.context.fetch = async (url, options) => {
    requests.push(url);
    if (url.endsWith('/api/applications')) return {ok: true, async text() {return '{"id":42}';}};
    if (url.endsWith('/autofill-plan')) {
      assert.deepEqual(JSON.parse(options.body).field_labels, ['First Name']);
      return {ok: true, async json() {return {filled: [{label: 'First Name', value: 'Demo'}], unmapped: []};}};
    }
    if (url.endsWith('/tailor-resume')) return pdfResponse();
    return pdfResponse();
  };
  const result = await w.context.runFillOnTab({id: 1, url: 'https://example.com/application'});
  assert.equal(result.resumeError, null);
  assert.ok(result.filled.includes('Resume/CV*'));
  assert.equal(requests.length, 3);
  assert.equal(result.applicationId, 42);
  assert.match(requests[0], /api\/applications$/);
  assert.match(requests[2], /tailor-resume$/);
});


test('autofill saves the bookmark even if scanning fails afterward', async () => {
  const w = resumeWorker();
  let saved = false;
  w.context.fetch = async (url, options) => {
    assert.match(url, /api\/applications$/);
    assert.equal(options.method, 'POST');
    saved = true;
    return {ok: true, async text() {return '{"id":42}';}};
  };
  w.context.chrome.scripting.executeScript = async () => {throw new Error('Page unavailable');};
  await assert.rejects(w.context.runFillOnTab({id: 1, url: 'https://example.com/job'}), /Page unavailable/);
  assert.equal(saved, true);
});

test('failed automatic bookmark stops autofill before touching the form', async () => {
  const w = resumeWorker();
  w.context.fetch = async () => ({ok: false, status: 401, async text() {return '{"error":"Log in first"}';}});
  await assert.rejects(w.context.runFillOnTab({id: 1, url: 'https://example.com/job'}), /Log in first/);
  assert.equal(w.uploads.length, 0);
});


test('returning to a posting overrides remembered application state', async () => {
  const w = worker(false);
  w.stored.submitted_1 = true;
  w.context.detectPageState = async () => ({hasIdentityFields: false, applyButtonText: 'Apply now'});
  const state = await w.context.getPageState({id: 1});
  assert.equal(state.hasIdentityFields, false);
  assert.equal(state.applyButtonText, 'Apply now');
  assert.equal(w.stored.started_1, false);
  assert.equal(w.stored.submitted_1, false);
});

test('later wizard steps keep Fill when there is no Apply control', async () => {
  const w = worker(false);
  w.context.detectPageState = async () => ({hasIdentityFields: false, applyButtonText: null});
  const state = await w.context.getPageState({id: 1});
  assert.equal(state.hasIdentityFields, true);
});

test('rejected file attachment raises an explicit resume error', async () => {
  const w = resumeWorker();
  w.context.fetch = async () => pdfResponse();
  w.context.chrome.scripting.executeScript = async options => [
    {result: options.world === 'MAIN' ? false : options.args[0]},
  ];
  await assert.rejects(w.upload(), /attachment could not be confirmed/);
});

test('CV-labelled upload is handled as a resume', async () => {
  const w = resumeWorker();
  w.context.fetch = async () => pdfResponse();
  await w.context.uploadResumeToUnmappedFileFields(
    {id: 1}, {fields: [{id: 2, kind: 'file', label: 'CV'}]}, 0, ['CV']);
  assert.equal(w.uploads.length, 1);
});
