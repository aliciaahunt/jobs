import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';

const coreRoot = process.env.OPENCLAW_CORE_ROOT;
assert.ok(coreRoot, 'Set OPENCLAW_CORE_ROOT to the installed OpenClaw package directory');
const sdk = name => pathToFileURL(path.join(coreRoot, 'dist', 'plugin-sdk', name + '.js')).href;
const { acquireFileLock, FILE_LOCK_STALE_ERROR_CODE, FILE_LOCK_TIMEOUT_ERROR_CODE } = await import(sdk('file-lock'));
const { enqueueKeyedTask } = await import(sdk('keyed-async-queue'));
const { resolveTimerTimeoutMs } = await import(sdk('number-runtime'));

const dir = process.env.RECOVERY_BUNDLE_DIR;
function region(text, label) {
  const start = text.indexOf('//#region ' + label);
  assert.notEqual(start, -1, label);
  return text.slice(start, text.indexOf('//#endregion', start));
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function harness(t) {
  const owner = region(await fs.readFile(path.join(dir, 'socket-close-CT__V2fz.mjs'), 'utf8'), 'extensions/whatsapp/src/connection-owner.ts')
    .replace('const GATEWAY_LOCAL_OWNER_WAIT_MS = 15e4;', 'const GATEWAY_LOCAL_OWNER_WAIT_MS = 500;')
    .replace('const INCUMBENT_CLEANUP_RETRY_MS = 5e3;', 'const INCUMBENT_CLEANUP_RETRY_MS = 10;');
  const controller = region(await fs.readFile(path.join(dir, 'connection-controller-CHo1oRJA.mjs'), 'utf8'), 'extensions/whatsapp/src/connection-controller.ts');
  const auth = region(await fs.readFile(path.join(dir, 'auth-store-BBonwKks.mjs'), 'utf8'), 'extensions/whatsapp/src/creds-persistence.ts');
  const queue = 'const CREDS_SAVE_FLUSH_TIMEOUT_MS = 20; const credsSaveQueues = new Map();\n' + auth.slice(auth.indexOf('function enqueueCredsSave('));
  const context = vm.createContext({
    fs, resolveUserPath: value => value, acquireFileLock,
    FILE_LOCK_STALE_ERROR_CODE, FILE_LOCK_TIMEOUT_ERROR_CODE,
    createDeferred: deferred, enqueueKeyedTask, resolveTimerTimeoutMs,
    setTimeout: (...args) => { const timer = setTimeout(...args); timer.unref = () => timer; return timer; },
    clearTimeout, setInterval, clearInterval, AbortController, console,
    DEFAULT_WHATSAPP_SOCKET_TIMING: {}, sleepWithAbort: async () => {},
    getWhatsAppChannelRuntime: () => ({ runtimeContexts: {} }),
    registerChannelRuntimeContext: () => ({ dispose() {} }),
    WHATSAPP_CONNECTION_OWNER_PENDING_CAPABILITY: 'test-pending',
    WHATSAPP_CONNECTION_CONTROLLER_CAPABILITY: 'test-controller',
    closeWhatsAppSocketAndWait: async () => {},
  });
  vm.runInContext(queue + '\n' + owner + '\n' + controller + '\nthis.Controller = WhatsAppConnectionController;', context);
  const authDir = await fs.mkdtemp(path.join(os.tmpdir(), 'whatsapp-recovery-test-'));
  const controllers = [];
  const writes = [];
  t.after(async () => {
    writes.forEach(write => write.resolve());
    for (const controller of controllers) controller.ownerAcquireAbortController.abort(new Error('test finished'));
    for (const controller of controllers) await controller.shutdown().catch(() => {});
    await fs.rm(authDir, { recursive: true, force: true });
    await fs.rm(authDir + '.lock', { force: true });
  });
  function make() {
    const controller = new context.Controller({ accountId: 'test', authDir, keepAlive: false });
    controllers.push(controller);
    return controller;
  }
  function pendingWrite() {
    const write = deferred(); writes.push(write);
    context.enqueueCredsSave(authDir, async () => {
      await write.promise;
      await fs.writeFile(path.join(authDir, 'synthetic-write.txt'), 'test credentials only');
    }, error => { throw error; });
    return write;
  }
  async function incumbentWithFailedShutdown() {
    const incumbent = make();
    await incumbent.ensureConnectionOwnership();
    const write = pendingWrite();
    incumbent.pendingSocketCleanup = { socketClosed: true, sock: {} };
    await assert.rejects(incumbent.shutdown(), /credential persistence did not drain/);
    assert.ok(incumbent.connectionOwnerLease);
    await fs.access(authDir + '.lock');
    return { incumbent, write };
  }
  return { context, make, pendingWrite, incumbentWithFailedShutdown, authDir };
}

test('replacement finishes the retained cleanup after the real credential queue drains', async t => {
  const h = await harness(t);
  const { incumbent, write } = await h.incumbentWithFailedShutdown();
  const replacement = h.make();
  setTimeout(() => write.resolve(), 30);
  await replacement.ensureConnectionOwnership();
  assert.equal(incumbent.connectionOwnerLease, null);
  assert.ok(replacement.connectionOwnerLease);
  assert.equal(await fs.readFile(path.join(h.authDir, 'synthetic-write.txt'), 'utf8'), 'test credentials only');
});

test('ownership is never handed off while credential persistence is pending', async t => {
  const h = await harness(t);
  const { incumbent, write } = await h.incumbentWithFailedShutdown();
  const replacement = h.make();
  const connecting = replacement.ensureConnectionOwnership();
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.ok(incumbent.connectionOwnerLease);
  assert.equal(replacement.connectionOwnerLease, null);
  write.resolve();
  await connecting;
  assert.equal(incumbent.connectionOwnerLease, null);
});

test('an undrained queue remains locked and reports the retained cleanup cause', async t => {
  const h = await harness(t);
  const { incumbent } = await h.incumbentWithFailedShutdown();
  const replacement = h.make();
  await assert.rejects(replacement.ensureConnectionOwnership(), error => {
    assert.match(error.message, /Another process owns/);
    assert.match(error.cause?.message ?? '', /credential persistence did not drain/);
    return true;
  });
  assert.ok(incumbent.connectionOwnerLease);
  assert.equal(replacement.connectionOwnerLease, null);
});

test('a healthy incumbent is not stopped by another connection attempt', async t => {
  const h = await harness(t);
  const incumbent = h.make();
  await incumbent.ensureConnectionOwnership();
  const replacement = h.make();
  const connecting = replacement.ensureConnectionOwnership();
  setTimeout(() => replacement.ownerAcquireAbortController.abort(new Error('cancelled')), 30);
  await assert.rejects(connecting, /cancelled/);
  assert.equal(incumbent.shuttingDown, false);
  assert.ok(incumbent.connectionOwnerLease);
});

test('standalone access never triggers recovery of an incumbent', async t => {
  const h = await harness(t);
  const { incumbent } = await h.incumbentWithFailedShutdown();
  let retries = 0;
  const shutdown = incumbent.shutdown.bind(incumbent);
  incumbent.shutdown = async () => { retries++; return shutdown(); };
  await assert.rejects(h.context.acquireWhatsAppStandaloneConnectionOwner(h.authDir), /Another process owns/);
  assert.equal(retries, 0);
});

test('concurrent shutdown calls share one cleanup attempt', async t => {
  const h = await harness(t);
  const incumbent = h.make();
  await incumbent.ensureConnectionOwnership();
  const write = h.pendingWrite();
  incumbent.pendingSocketCleanup = { socketClosed: true, sock: {} };
  let cleanupCalls = 0;
  const finish = incumbent.finishPendingSocketCleanup.bind(incumbent);
  incumbent.finishPendingSocketCleanup = async () => { cleanupCalls++; return finish(); };
  const results = [incumbent.shutdown(), incumbent.shutdown()];
  write.resolve();
  await Promise.all(results);
  assert.equal(cleanupCalls, 1);
});
