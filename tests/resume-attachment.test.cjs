const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('extension/background.js', 'utf8');
const attachSource = source.slice(source.indexOf('async function attachResumeFile('), source.indexOf('async function runFillOnTab('));

async function attach({ replace = false, showAfter = Infinity, retain = false, preexisting = false }) {
  let ticks = 0;
  const body = { innerText: preexisting ? 'master_resume.pdf' : '' };
  const input = {
    isConnected: true, files: [],
    dispatchEvent(event) {
      if (event.type === 'change' && !retain) {
        this.files = [];
        if (replace) this.isConnected = false;
      }
    },
  };
  const context = vm.createContext({
    document: { body, querySelector: () => input },
    CSS: { escape: String }, Uint8Array, atob,
    Event: class { constructor(type) { this.type = type; } },
    File: class { constructor(parts, name) { this.name = name; this.size = parts[0].length; } },
    DataTransfer: class {
      constructor() { this.files = []; this.items = { add: file => this.files.push(file) }; }
    },
    setTimeout(callback) {
      ticks++;
      if (ticks >= showAfter) body.innerText = 'master_resume.pdf';
      callback();
    },
  });
  vm.runInContext(attachSource, context);
  return context.attachResumeFile(1, btoa('%PDF'), 'master_resume.pdf', 'application/pdf');
}

test('accepts a delayed attachment chip after the input is replaced', async () => {
  assert.equal(await attach({ replace: true, showAfter: 8 }), true);
});
test('accepts a delayed attachment chip when the same input is cleared', async () => {
  assert.equal(await attach({ showAfter: 4 }), true);
});
test('accepts native file retention', async () => {
  assert.equal(await attach({ retain: true }), true);
});
test('does not claim success when a rejected file never appears', async () => {
  assert.equal(await attach({}), false);
});
test('an already visible filename does not confirm a failed new attachment', async () => {
  assert.equal(await attach({ preexisting: true }), false);
});
