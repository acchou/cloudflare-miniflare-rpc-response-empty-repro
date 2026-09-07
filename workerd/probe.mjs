import assert from "node:assert/strict";
import { get as getHttp } from "node:http";
import { get as getHttps } from "node:https";
import { test } from "node:test";
import { gunzipSync } from "node:zlib";

const baseUrl = process.env.REPRO_URL ?? "http://127.0.0.1:8080";
const get = new URL(baseUrl).protocol === "https:" ? getHttps : getHttp;
const routes = [
  ["RPC Response survives await + gzip", "/", "gzip"],
  ["control: no await", "/no-await", "gzip"],
  ["control: no gzip, original Response", "/no-gzip", undefined],
  ["control: no gzip, wrapped Response", "/wrapped-no-gzip", undefined],
  ["control: no RPC", "/no-rpc", "gzip"]
];

for (const [label, pathname, expectedEncoding] of routes) {
  test(label, async t => {
    // node:http preserves compressed bytes; fetch can expose truncated gzip as
    // an empty body. Strict decompression also checks the gzip trailer.
    const { status, encoding, raw } = await new Promise((resolve, reject) => {
      const request = get(new URL(pathname, baseUrl), {
        headers: { "Accept-Encoding": "gzip" },
        // Keep a broken response's connection out of subsequent control cases.
        agent: false,
        signal: AbortSignal.timeout(10_000)
      }, response => {
        const chunks = [];
        response.on("data", chunk => chunks.push(chunk));
        response.on("error", reject);
        response.on("end", () => resolve({
          status: response.statusCode,
          encoding: response.headers["content-encoding"],
          raw: Buffer.concat(chunks)
        }));
      });
      request.on("error", reject);
    });

    t.diagnostic(JSON.stringify({
      status,
      encoding: encoding ?? "identity",
      wireBytes: raw.length,
      wireHex: raw.toString("hex")
    }));
    assert.equal(status, 200);
    assert.equal(encoding, expectedEncoding);
    const body = encoding === "gzip" ? gunzipSync(raw) : raw;
    assert.deepEqual(JSON.parse(body.toString("utf8")), { ok: true });
  });
}
