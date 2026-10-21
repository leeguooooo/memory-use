#!/usr/bin/env node
// Execute the pinned upstream state machine with injected Git/backend I/O.
// This tests branching, not Letta's hosted sync, hooks, or transport retries.
const fs = require('node:fs');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');

const path = process.argv[2];
if (!path) throw new Error('usage: node letta_sync_probe.cjs /path/to/memory-git.ts');
const source = fs.readFileSync(path, 'utf8');
const digest = crypto.createHash('sha256').update(source).digest('hex');
assert.equal(digest, 'd219497982a9b47de810dcd4af3bcfef1a8b22db5c40a34e987e06a174c4fb35', 'upstream snapshot changed');
const anchor = 'export async function syncPendingMemoryCommitsAfterTurn(';
const signatureEnd = '): Promise<MemoryPostTurnSyncResult> {';
const start = source.indexOf(signatureEnd, source.indexOf(anchor)) + signatureEnd.length;
const end = source.indexOf('\n}\n\n/**', start);
assert(start >= signatureEnd.length && end > start, 'could not isolate upstream function');
let body = source.slice(start, end);
const backendImport = 'const { getBackend } = await import("@/backend");';
assert.equal(body.split(backendImport).length, 2, 'expected one backend import');
body = body.replace(backendImport, 'const { getBackend } = backendModule;');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

async function run(name, options, expected) {
  const calls = [];
  let conflictChecks = 0;
  const env = {
    backendModule: {getBackend: () => ({capabilities: {localMemfs: false, remoteMemfs: true}})},
    existsSync: () => true,
    join: (...parts) => parts.join('/'),
    getScopedMemoryFilesystemRoot: () => '/synthetic/memory',
    runGit: async () => ({stdout: options.dirty ? ' M note.md\n' : ''}),
    getMemoryConflictSummary: async () => {
      conflictChecks++;
      return options.conflict || (options.conflictAfterPull && conflictChecks > 1) ? 'synthetic merge conflict' : null;
    },
    getAuthToken: async () => 'synthetic-not-a-credential',
    prepareMemoryRepoForGitOps: async () => {},
    getMemoryAheadBehind: async () => options.unknown ? null : ({ahead: options.clean ? 0 : 1, behind: 0}),
    invalidPendingMemory: () => options.invalid ? {status: 'invalid', summary: 'synthetic validation failure'} : null,
    runGitWithRetry: async (_dir, args) => {
      calls.push(args[0]);
      if (options.pushFailure && args[0] === 'push') throw new Error('synthetic offline');
      if (options.nonFastForward && calls.length === 1) throw new Error('synthetic non-fast-forward');
    },
    isNonFastForwardPushError: error => error.message.includes('non-fast-forward'),
  };
  const invoke = new AsyncFunction(...Object.keys(env), 'agentId', 'options', body);
  const result = await invoke(...Object.values(env), 'synthetic-agent', {});
  assert.equal(result.status, expected, name);
  return {name, status: result.status, calls};
}

(async () => {
  const cases = [];
  cases.push(await run('dirty checkout', {dirty: true}, 'dirty'));
  cases.push(await run('existing conflict', {conflict: true}, 'conflict'));
  cases.push(await run('no pending commit', {clean: true}, 'clean'));
  cases.push(await run('push rejected/offline', {pushFailure: true}, 'push_failed'));
  cases.push(await run('next turn retries pending commit', {}, 'pushed'));
  cases.push(await run('non-fast-forward rebases and retries', {nonFastForward: true}, 'pushed'));
  cases.push(await run('post-pull conflict blocks second push', {nonFastForward: true, conflictAfterPull: true}, 'conflict'));
  cases.push(await run('invalid history blocks push', {invalid: true}, 'invalid'));
  cases.push(await run('unknown divergence returns clean', {unknown: true}, 'clean'));
  assert.deepEqual(cases[0].calls, []);
  assert.deepEqual(cases[3].calls, ['push']);
  assert.deepEqual(cases[4].calls, ['push']);
  assert.deepEqual(cases[5].calls, ['push', 'pull', 'push']);
  assert.deepEqual(cases[6].calls, ['push', 'pull']);
  assert.deepEqual(cases[7].calls, []);
  assert.deepEqual(cases[8].calls, []);
  process.stdout.write(JSON.stringify({
    revision: 'c25f091d0fce1f032e5cdf67ce131d5b474e6576', source_sha256: digest,
    extraction: 'original function body; one backend dynamic import replaced with injected module; TypeScript signature removed',
    scope: 'real upstream branching with synthetic Git/backend dependencies; not hosted or transport verification',
    cases,
  }, null, 2) + '\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
