#!/usr/bin/env node
// Run pinned upstream lock modules with actual files, Git, ps, and child processes.
import assert from 'node:assert/strict';
import {execFileSync, spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {once} from 'node:events';
import {mkdtemp, readFile, writeFile, mkdir, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

const upstream = process.argv[2];
if (!upstream) throw new Error('usage: node letta_lock_probe.mjs /directory/with/pinned/modules');
const hashes = {
  'file-lock.ts': 'fe70a13fad842c406cd480979b47871be2636e1dd369c14f95e9f15c3b0b62fc',
  'memory-git-dir.ts': '101e8c4e459015b3459dcf18dc378e58ff5f3136d1a28e3aaa2c3d9371c5f9bb',
  'memory-operation.ts': 'ad2ed4fe2a5db068748ca653b7e9e94a337685f1d114ef3b6bbfef6d18ba514c',
  'sleep.ts': '4b03237aefe20e8560dbf23ee19c8008005f3064d876193c4c0f20cc8c2ed884',
  'process-liveness.ts': 'b6507ec79ebcfa297ebc7411350c9c8c87024909824d9d1d848e6f0fc08a8f5f',
};
const scratch = await mkdtemp(join(tmpdir(), 'letta-lock-replay-'));
const children = new Map();
const releases = new Set();
const results = [];
try {
  await writeFile(join(scratch, 'package.json'), '{"type":"module"}\n');
  for (const [name, hash] of Object.entries(hashes)) {
    const source = await readFile(join(upstream, name), 'utf8');
    assert.equal(createHash('sha256').update(source).digest('hex'), hash, name);
    // Only import paths change. Node strips TypeScript types; no I/O is mocked.
    const runtime = source.replaceAll('@/agent/memory-git-dir', './memory-git-dir.ts')
      .replaceAll('@/utils/file-lock', './file-lock.ts')
      .replaceAll('@/utils/process-liveness', './process-liveness.ts')
      .replaceAll('@/utils/sleep', './sleep.ts');
    await writeFile(join(scratch, name), runtime);
  }
  const moduleURL = pathToFileURL(join(scratch, 'memory-operation.ts')).href;
  const {claimMemoryOperation, getMemoryOperationPath} = await import(moduleURL);
  const repo = join(scratch, 'repo');
  await mkdir(repo);
  execFileSync('git', ['init', '-q', '-b', 'main', repo]);
  execFileSync('git', ['-C', repo, '-c', 'user.name=synthetic', '-c', 'user.email=synthetic@example.com', 'commit', '--allow-empty', '-qm', 'seed']);
  const lock = await getMemoryOperationPath(repo);
  async function hold() {
    const release = await claimMemoryOperation(repo);
    assert(release);
    releases.add(release);
    return async () => {await release(); releases.delete(release);};
  }
  async function child(mode, cwd=repo, env={}) {
    const code = `const {claimMemoryOperation}=await import(process.argv[1]); const release=await claimMemoryOperation(process.argv[2]); console.log(release ? 'acquired' : 'blocked'); if(release && process.argv[3]==='hold') setInterval(()=>{},1000); else await release?.();`;
    const p = spawn(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e', code, moduleURL, cwd, mode], {
      env: {...process.env, ...env}, stdio: ['ignore', 'pipe', 'pipe'],
    });
    const exited = once(p,'exit');
    children.set(p,exited);
    p.stderr.resume();
    const text = await new Promise((accept, reject) => {
      const timer=setTimeout(()=>reject(new Error('child readiness timed out')), 5000);
      p.stdout.once('data', data => {clearTimeout(timer); accept(String(data).trim());});
      p.once('error', err => {clearTimeout(timer); reject(err);});
      p.once('exit', code => {if(code) {clearTimeout(timer); reject(new Error('child exited '+code));}});
    });
    if(mode!=='hold') {
      const [code] = await exited;
      assert.equal(code,0);
      children.delete(p);
    }
    return {p,text,exited};
  }
  const now = Date.now;
  let release;
  try {
    Date.now = () => 1; // Set the actual acquisition timestamp; preserve owner identity.
    release = await hold();
  } finally { Date.now = now; }
  const blocked = await child('try', repo, {TZ:'Asia/Kolkata'});
  assert.equal(blocked.text,'blocked');
  results.push({case:'live owner with old acquisition timestamp, other timezone',passed:true});
  const linked = join(scratch,'linked');
  execFileSync('git',['-C',repo,'worktree','add','-qb','linked',linked]);
  assert.equal((await child('try',linked)).text,'acquired');
  results.push({case:'linked worktree independent of owned checkout',passed:true});
  await release();
  assert.equal((await child('try')).text,'acquired');
  results.push({case:'explicit release permits next process',passed:true});
  const holder = await child('hold');
  assert.equal(holder.text,'acquired');
  holder.p.kill('SIGKILL');
  await holder.exited;
  children.delete(holder.p);
  release = await hold();
  await release();
  results.push({case:'terminated holder reclaimed using actual process liveness',passed:true});
  await writeFile(lock,JSON.stringify({pid:process.pid, started:'1970-01-01', acquiredAt:now(), token:'synthetic'}));
  release = await hold();
  await release();
  results.push({case:'synthetic reused-pid identity record reclaimed',passed:true});
  console.log(JSON.stringify({revision:'c25f091d0fce1f032e5cdf67ce131d5b474e6576',source_sha256:hashes,
    scope:'actual upstream modules, rewritten import paths and Node type stripping only; real local Git, files, ps and child processes; not original Bun suite or hosted Letta',
    node:process.version,cases:results},null,2));
} finally {
  for(const [child,exited] of children) {
    if(child.exitCode===null && child.signalCode===null) child.kill('SIGKILL');
    await exited.catch(()=>{});
  }
  for(const release of releases) await release();
  await rm(scratch,{recursive:true,force:true});
}
