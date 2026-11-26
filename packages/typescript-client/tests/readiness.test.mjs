import assert from 'node:assert/strict';
import { setImmediate } from 'node:timers/promises';
import { test } from 'node:test';
import { AbortError, MemoryApiError, MemoryClient, RequestTimeoutError, TimeoutError } from '../dist/index.js';

const versionId = '10000000-0000-0000-0000-000000000001';

function report({ ready }) {
  return Response.json({
    ready,
    versions: [{
      version_id: versionId,
      ready,
      stages: [{ stage: 'claims', status: ready ? 'succeeded' : 'running', component_version: 'fixture' }],
    }],
    capabilities: {},
  });
}

async function advance(t, milliseconds) {
  t.mock.timers.tick(milliseconds);
  await setImmediate();
}

const transientFailures = {
  '524': () => new Response('<html>Origin timeout</html>', { status: 524 }),
  '502': () => new Response(null, { status: 502 }),
  '504': () => new Response(null, { status: 504 }),
  'connection reset': () => { throw Object.assign(new Error('connection reset'), { code: 'ECONNRESET' }); },
};

for (const [name, fail] of Object.entries(transientFailures)) {
  test(`readiness wait recovers from ${name} while respecting the poll interval`, async t => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    let calls = 0;
    const client = new MemoryClient({ client: { request: async ({ path }) => {
      assert.equal(path, '/readiness');
      return ++calls === 1 ? fail() : report({ ready: true });
    } } });
    try {
      const waiting = client.waitForReadiness({ versionIds: [versionId], pollIntervalMs: 3000 });
      await setImmediate();
      assert.equal(calls, 1);
      await advance(t, 2999);
      assert.equal(calls, 1);
      await advance(t, 1);
      assert((await waiting).ready);
      assert.equal(calls, 2);
    } finally {
      client.close();
    }
  });
}

test('persistent 503 reaches the readiness deadline with the last report and error', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0;
  const client = new MemoryClient({ client: { request: async () => {
    calls++;
    if (calls === 1) return report({ ready: false });
    if (calls < 4) return new Response('Deployment starting', { status: 503 });
    return Response.json({ detail: {
      code: 'engine_unavailable', message: 'Deployment restarting', retryable: true, request_id: 'latest-check',
    } }, { status: 503 });
  } } });
  try {
    const timeout = assert.rejects(
      client.waitForReadiness({ versionIds: [versionId], timeoutMs: 12000, pollIntervalMs: 3000 }),
      error => {
        assert(error instanceof TimeoutError);
        assert.equal(error.report.versions[0].stages[0].status, 'running');
        assert(error.lastError instanceof MemoryApiError);
        assert.equal(error.lastError.statusCode, 503);
        assert.equal(error.lastError.code, 'engine_unavailable');
        assert.equal(error.lastError.retryable, true);
        assert.equal(error.lastError.requestId, 'latest-check');
        assert.equal(error.cause, error.lastError);
        assert.match(error.message, /Deployment restarting/);
        return true;
      },
    );
    await setImmediate();
    await advance(t, 3000);
    await advance(t, 3000);
    assert.equal(calls, 3);
    await advance(t, 3999);
    assert.equal(calls, 3);
    await advance(t, 1);
    assert.equal(calls, 4);
    await advance(t, 2000);
    await timeout;
    assert.equal(calls, 4);
  } finally {
    client.close();
  }
});

test('per-request timeouts are retried until the readiness deadline even without a report', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0;
  const client = new MemoryClient({ timeoutMs: 5, client: { request: async () => {
    calls++;
    return new Promise(() => {});
  } } });
  try {
    const timeout = assert.rejects(
      client.waitForReadiness({ versionIds: [versionId], timeoutMs: 3000, pollIntervalMs: 1 }),
      error => error instanceof TimeoutError && error.report === undefined && error.lastError instanceof RequestTimeoutError,
    );
    await advance(t, 5);
    await advance(t, 2000);
    assert.equal(calls, 2);
    await advance(t, 5);
    await advance(t, 990);
    await timeout;
    assert.equal(calls, 2);
  } finally {
    client.close();
  }
});

for (const status of [401, 503]) {
  test(`readiness wait stops on non-retryable ${status}`, async () => {
    let calls = 0;
    const client = new MemoryClient({ client: { request: async () => {
      calls++;
      return Response.json({ detail: { message: 'Refused', retryable: false } }, { status });
    } } });
    try {
      await assert.rejects(
        client.waitForReadiness({ versionIds: [versionId], timeoutMs: 500 }),
        error => error instanceof MemoryApiError && error.statusCode === status,
      );
      assert.equal(calls, 1);
    } finally {
      client.close();
    }
  });
}

test('cancelling a readiness wait during backoff stops polling immediately', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const controller = new AbortController();
  let calls = 0;
  const client = new MemoryClient({ client: { request: async () => {
    calls++;
    return new Response(null, { status: 503 });
  } } });
  try {
    const aborted = assert.rejects(
      client.waitForReadiness({ versionIds: [versionId], signal: controller.signal }),
      error => error instanceof AbortError && error.cause === 'cancelled',
    );
    await setImmediate();
    controller.abort('cancelled');
    await aborted;
    await advance(t, 30000);
    assert.equal(calls, 1);
  } finally {
    client.close();
  }
});
