# workerd truncates an RPC Response during gzip compression

A Worker receives a `Response` over RPC, waits once, and returns it with gzip
compression. The client receives **HTTP 200 with an incomplete gzip stream**.
In Node's `fetch`, this appears as an empty body. A strict gzip decoder rejects it.

**Still reproduced on September 6, 2026:** workerd `1.20260906.1` and Wrangler
`4.129.0`. This repo pins those versions. It runs locally with no Cloudflare
account, credentials, deployment, or application dependencies.

## Quick reproduction with Wrangler

Requires Node.js 22+ and pnpm 11.7.0; validated on macOS ARM64 with Node 24.13.1.
Run from this directory:

```sh
pnpm install --frozen-lockfile
# Ensure an inherited development workaround does not hide the bug:
unset WRANGLER_DISABLE_REQUEST_BODY_DRAINING
pnpm test
```

**Expected while the bug exists: exit 1, one failed test and three passing controls.**
The tests assert correct behavior; they do not treat broken responses as success.
The harness starts and stops its own local Workers on an allocated port.

| Test | Affected runtime |
| --- | --- |
| Forward an RPC Response from `POST /direct` with gzip | Fails: empty decoded body |
| Same response from `GET /direct` | Passes |
| `POST /direct` with `Accept-Encoding: identity` | Passes |
| Read the RPC body into memory before returning it (`POST /materialized`) | Passes |

To check the Wrangler-specific workaround:

```sh
WRANGLER_DISABLE_REQUEST_BODY_DRAINING=1 pnpm test
```

**Expected: exit 0, all four tests pass.** Upgrading to Wrangler `4.129.0` alone
does not resolve the failure.

### Why POST triggers it

Wrangler adds request-body-draining middleware that performs an extra `await`
before returning a non-GET response. Miniflare supplies automatic gzip compression.
Together, these expose the underlying runtime defect. POST itself is not required:
the pure-workerd example below fails on GET with an explicit timer await.

## Pure-workerd reproduction

This removes Wrangler and Miniflare from the execution path. After installing the
pinned dependencies above, start the server in one terminal:

```sh
pnpm workerd:serve
```

In a second terminal, from this directory:

```sh
pnpm workerd:probe
```

The server listens on `127.0.0.1:8080`. Stop it with Ctrl-C after testing. If that
port is already in use, choose a free port and pass the same address to both commands:

```sh
# Terminal 1
pnpm workerd:serve -s http=127.0.0.1:8081
# Terminal 2
REPRO_URL=http://127.0.0.1:8081 pnpm workerd:probe
```

The relevant code is [caller.mjs](workerd/caller.mjs):

```js
let response = await env.CALLEE.getJson();
await new Promise(resolve => setTimeout(resolve, 0));
response = new Response(response.body, response);
response.headers.set("Content-Encoding", "gzip");
return response;
```

The [callee](workerd/callee.mjs) simply returns `Response.json({ ok: true })`.

**Expected while affected: exit 1, one failed test and three passing controls.**
The probe checks HTTP status, encoding, strict decompression, and the exact JSON
value for every route. It has a 10-second request deadline. Exit 0 means all four
cases passed; a startup, connection, or control failure is not evidence of this bug.
Each case uses a fresh connection so the broken response cannot contaminate the
following controls through connection reuse.

| Route | RPC | Timer await | Gzip | Expected while affected |
| --- | --- | --- | --- | --- |
| `/` | Yes | Yes | Yes | Fails |
| `/no-await` | Yes | No | Yes | Passes |
| `/no-gzip` | Yes | Yes | No | Passes |
| `/no-rpc` | No | Yes | Yes | Passes |

The failed case reports 10 raw wire bytes:

```text
1f8b0800000000000013
```

These are only a gzip header: the payload and trailer are missing. The probe fails
in `gunzipSync` with an unexpected-end-of-file error. Passing gzip cases contain
31 bytes and decode to `{"ok":true}`. workerd also logs:

```text
ReadableStream received over RPC disconnected prematurely.
```

This identifies the failing stream path, but does not establish the exact C++
ownership or lifetime bug.

## Versions and evidence

| Package | Version | Result |
| --- | --- | --- |
| workerd | `1.20260804.1` | Last good tested; all original cases passed 10/10 |
| workerd | `1.20260807.2` | First bad tested; affected case failed 10/10 |
| workerd | `1.20260906.1` | Affected case failed 10/10; both original controls passed 10/10 |
| Wrangler | `4.121.0` | Historical passing control; bundles workerd `1.20260804.1` |
| Wrangler | `4.123.0` | Historical failure; bundles workerd `1.20260811.1` |
| Wrangler | `4.129.0` | One failure, three passing controls; bundles workerd `1.20260903.1` |

The three pure-workerd rows were rerun on September 6. After adding `/no-rpc` and
strict byte validation, the current probe was run three times against
`1.20260906.1`: every run had exactly one failure and three passing controls. It
also passed all four cases on `1.20260804.1`. The first-bad/last-good versions bound
the regression; they do not identify the introducing commit.

The latest pure runtime failed with both compatibility dates `2026-04-09` and
`2026-09-06`; changing the date did not fix it. This repo retains `2026-04-09` so
older runtime comparisons remain possible. The direct `workerd` dependency pins
the pure example separately from the runtime bundled with Wrangler.

## Why this is a bug, and its scope

Cloudflare documents that [Responses and body streams can be transferred over
RPC](https://developers.cloudflare.com/workers/runtime-apis/rpc/#readablestream-writeablestream-request-and-response),
with ownership transferred to the recipient. It also documents that
[Response encoding is automatic by default](https://developers.cloudflare.com/workers/runtime-apis/response/#parameters):
setting `Content-Encoding: gzip` asks the runtime to compress the body. The example
uses those APIs without consuming, canceling, or disposing the received body.

The confirmed scope is **local workerd and Wrangler**. Current deployed Workers
behavior has not been tested here. Do not infer production exposure or a production
fix from this local result. Targeted upstream issue/PR searches on September 6
found no exact match; that is not proof no related report exists.

## Workarounds and tradeoffs

- **Wrangler development:** disable request-body draining as shown above. This
  avoids the middleware's extra await; it does not fix application code that
  explicitly awaits before returning an RPC Response.
- **Buffer the body:** read `await response.arrayBuffer()` before returning a new
  Response, as the `/materialized` control does. This sacrifices streaming and
  increases memory use for large responses.
- **Avoid gzip on this path:** use `Accept-Encoding: identity` in the Wrangler
  client, or omit gzip encoding in the pure example. This changes compression.
- **Older runtime:** `1.20260804.1` is a known passing comparison. Downgrading is
  useful for regression diagnosis, not a general recommendation to stay on old tooling.
