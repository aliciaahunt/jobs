# OpenClaw WhatsApp retained-owner recovery

This directory records the tested local repair used for WhatsApp delivery of
these job reports. It is operator maintenance for an existing OpenClaw install;
it is not part of the job collectors.

## Problem and scope

After a WhatsApp connection closes, pending credential writes can outlast the
shutdown deadline. OpenClaw correctly retains exclusive ownership, but the
replacement connection cannot retry the unfinished cleanup. It repeatedly
reports `Another process owns this WhatsApp connection`, even when the owner
is the same Gateway process. A whole-Gateway restart restores connectivity.

This backport lets a replacement request cleanup from the incumbent controller.
Cleanup is single-flight, and ownership is released only after transport closure
and credential persistence finish. Healthy owners are left alone. Standalone
lookups cannot trigger cleanup, and permanently blocked writes remain locked.

The repair is adapted from upstream
[PR #167360](https://github.com/openclaw/openclaw/pull/167360), reviewed head
`a567dafc91e32163a068c442d4da8599fcb1ea0e`. It is a local package patch, not an
official release. The installed baseline was OpenClaw and `@openclaw/whatsapp`
**2026.9.9**. The 2026.10.6 stable ownership source still lacked this mechanism
when checked on 23 October 2026.

`patch-manifest.json` records the exact supported bundle hashes. Preparation
refuses any different baseline, and verifies the prepared output hashes.
No credentials, account identifiers, private configuration, reports, or
installed third-party bundles are included in this repository.

## Files

- `recovery.patch`: the two internal JavaScript bundle changes.
- `prepare_patch.py`: prepares candidate bundles from an exact matching baseline;
  it never edits installed files or restarts a service.
- `recovery.test.mjs`: six regression tests using the bundled controller and owner,
  real SDK file locks and the real credential queue, with delayed synthetic file
  writes in temporary directories. No live WhatsApp credentials or messages are used.
- `patch-manifest.json`: versions, upstream provenance, and before/after hashes.
- `LICENSE.openclaw`: the upstream licence for the derived code.

## Prepare and test

Run in a writable copy of this directory, as the service account, with the same
Node runtime used by OpenClaw. Set these paths to the actual installed packages:

```sh
export OPENCLAW_CORE_ROOT=/path/to/node_modules/openclaw
export WHATSAPP_SETUP_DIR=/path/to/node_modules/@openclaw/whatsapp/dist/.setup

mkdir -p original
cp "$WHATSAPP_SETUP_DIR/socket-close-CT__V2fz.mjs" original/
cp "$WHATSAPP_SETUP_DIR/connection-controller-CHo1oRJA.mjs" original/
cp "$WHATSAPP_SETUP_DIR/auth-store-BBonwKks.mjs" original/

python3 prepare_patch.py

RECOVERY_BUNDLE_DIR="$PWD/original" node --test --test-concurrency=1 recovery.test.mjs
RECOVERY_BUNDLE_DIR="$PWD/candidate" node --test --test-concurrency=1 recovery.test.mjs
```

Expected baseline result: four failing and two passing tests. Expected candidate:
six passing tests. Test-only VM copies shorten the ownership and credential-drain
waits; production retains its 150-second ownership budget and 5-second cleanup
retry interval. The transport is not exercised by these regression tests.

## Deployment and verification

An administrator must stop the existing Gateway cleanly, retain copies of the
original bundles, atomically replace only the two changed files while preserving
their owner and permissions, then restart the Gateway. Do not remove a live
account lock, clear credentials, or relax access rules. If startup or connection
verification fails, restore the original bundles and restart.

After deployment, verify `openclaw channels status --json` reports WhatsApp
connected and healthy. Check that the existing daily automation is still enabled
with its original schedule and delivery target. In this deployment all six tests
passed against installed code and the actual linked connection was healthy.
This does not establish that every possible WhatsApp/network outage will recover.

A future package update may replace the local patch. Review the upstream outcome
before updating; do not reapply these pinned replacements to a different version.
