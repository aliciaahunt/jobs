from pathlib import Path
import hashlib,json,shutil
root=Path(__file__).resolve().parent
expected=json.loads((root/'patch-manifest.json').read_text())
for name, hashes in expected['files'].items():
    if hashlib.sha256((root/'original'/name).read_bytes()).hexdigest()!=hashes['before']:
        raise SystemExit('Unsupported bundle baseline; refusing to prepare a patch: '+name)
candidate=root/'candidate'
shutil.copytree(root/'original',candidate,dirs_exist_ok=True)
def replace_once(text,before,after):
    if text.count(before)!=1:
        raise ValueError('Unexpected bundle baseline: '+before[:120])
    return text.replace(before,after,1)
owner=candidate/'socket-close-CT__V2fz.mjs'
text=owner.read_text()
text=replace_once(text,'const GATEWAY_LOCAL_OWNER_WAIT_MS = 15e4;', 'const GATEWAY_LOCAL_OWNER_WAIT_MS = 15e4;\nconst INCUMBENT_CLEANUP_RETRY_MS = 5e3;')
start='''function startIncumbentCleanup(owner) {
	const retry = owner.retryCleanup;
	if (!retry || owner.cleanupAttempt) return;
	const attempt = Promise.resolve().then(retry).then(() => {
		owner.lastCleanupError = owner.retryCleanup ? new Error("WhatsApp connection owner still holds this account") : void 0;
	}, (error) => {
		owner.lastCleanupError = error;
	});
	const settled = attempt.finally(() => {
		if (owner.cleanupAttempt === settled) owner.cleanupAttempt = null;
	});
	owner.cleanupAttempt = settled;
}
'''
text=replace_once(text,'async function reserveProcessOwner(params) {\n\twhile (true)',start+'async function reserveProcessOwner(params) {\n\tlet waitingOn;\n\tlet deadline = 0;\n\twhile (true)')
text=replace_once(text,'\t\t\t\ttoken: Symbol(params.ownerPath)\n','\t\t\t\ttoken: Symbol(params.ownerPath),\n\t\t\t\tretryCleanup: null,\n\t\t\t\tcleanupAttempt: null,\n\t\t\t\tlastCleanupError: void 0\n')
text=replace_once(text,'''\t\tif (!params.waitForLocalOwner) throw new WhatsAppConnectionOwnerBusyError(params.authDir);
		let timer;''','''\t\tif (!params.waitForLocalOwner) throw new WhatsAppConnectionOwnerBusyError(params.authDir);
		if (waitingOn !== current) {
			waitingOn = current;
			deadline = Date.now() + GATEWAY_LOCAL_OWNER_WAIT_MS;
		}
		const remainingMs = deadline - Date.now();
		if (remainingMs <= 0) throw new WhatsAppConnectionOwnerBusyError(params.authDir, current.lastCleanupError === void 0 ? void 0 : { cause: current.lastCleanupError });
		startIncumbentCleanup(current);
		let timer;''')
text=replace_once(text,', GATEWAY_LOCAL_OWNER_WAIT_MS);',', Math.min(remainingMs, INCUMBENT_CLEANUP_RETRY_MS));')
text=replace_once(text,'\t\tif (outcome === "timed_out") throw new WhatsAppConnectionOwnerBusyError(params.authDir);\n','')
text=replace_once(text,'\tprocessOwners.delete(ownerPath);\n\towner.resolveReleased();','\tprocessOwners.delete(ownerPath);\n\towner.retryCleanup = null;\n\towner.resolveReleased();')
text=replace_once(text,'\treturn { release: async () => {','''\treturn {
		setCleanupRetry: (retry) => {
			if (processOwners.get(ownerPath)?.token === processOwner.token) processOwner.retryCleanup = retry;
		},
		release: async () => {''')
owner.write_text(text)
controller=candidate/'connection-controller-CHo1oRJA.mjs'
text=controller.read_text()
text=replace_once(text,'\t\tthis.retainedOwnerReleaseLease = null;\n\t\tthis.connectionOwnerLeasePromise = null;','\t\tthis.retainedOwnerReleaseLease = null;\n\t\tthis.shutdownPromise = null;\n\t\tthis.connectionOwnerLeasePromise = null;')
text=replace_once(text,'\tasync shutdown() {\n\t\tthis.shuttingDown = true;', '''\tasync shutdown() {
		if (!this.shutdownPromise) {
			const attempt = this.runShutdown().finally(() => {
				if (this.shutdownPromise === attempt) this.shutdownPromise = null;
			});
			this.shutdownPromise = attempt;
		}
		await this.shutdownPromise;
	}
	async runShutdown() {
		this.shuttingDown = true;''')
text=replace_once(text,'\t\t\tconst ownerLease = await acquireWhatsAppGatewayConnectionOwner(this.authDir, this.ownerAcquireAbortController.signal);\n\t\t\ttry {','''\t\t\tconst ownerLease = await acquireWhatsAppGatewayConnectionOwner(this.authDir, this.ownerAcquireAbortController.signal);
			ownerLease.setCleanupRetry(async () => {
				if (this.shuttingDown) await this.shutdown();
			});
			try {''')
controller.write_text(text)
for name, hashes in expected['files'].items():
    if hashlib.sha256((candidate/name).read_bytes()).hexdigest()!=hashes['after']:
        raise SystemExit('Prepared bundle checksum does not match the tested repair: '+name)
print('Prepared the verified two-file repair in candidate/. No installed files were changed.')
