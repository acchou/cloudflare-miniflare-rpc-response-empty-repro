import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createTestHarness } from "wrangler";

let harness;
let harnessUrl;

before(async () => {
  harness = createTestHarness({
    root: import.meta.dirname,
    workers: [
      { configPath: "../wrangler.gateway.jsonc" },
      { configPath: "../wrangler.account.jsonc" }
    ]
  });
  harnessUrl = (await harness.listen()).url;
});

after(async () => {
  await harness.close();
});

test("directly forwards an RPC Response body for POST", async () => {
  const response = await fetch(new URL("/direct", harnessUrl), { method: "POST" });
  const body = await response.text();

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-encoding"), "gzip");
  assert.ok(body.length > 0, "expected the RPC Response body to be non-empty");
  assert.deepEqual(JSON.parse(body), { ok: true });
});

test("directly forwards the same RPC Response body for GET", async () => {
  const response = await fetch(new URL("/direct", harnessUrl));

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-encoding"), "gzip");
  assert.deepEqual(await response.json(), { ok: true });
});

test("directly forwards the RPC Response for POST without gzip", async () => {
  const response = await fetch(new URL("/direct", harnessUrl), {
    headers: { "accept-encoding": "identity" },
    method: "POST"
  });

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-encoding"), null);
  assert.deepEqual(await response.json(), { ok: true });
});

test("materializing the RPC Response preserves its body for POST", async () => {
  const response = await fetch(new URL("/materialized", harnessUrl), {
    method: "POST"
  });
  const body = await response.text();

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-encoding"), "gzip");
  assert.ok(body.length > 0, "expected the RPC Response body to be non-empty");
  assert.deepEqual(JSON.parse(body), { ok: true });
});
