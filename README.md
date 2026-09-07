# RPC Response streaming: premature disconnects and truncated bodies

A Worker receives **plain JSON over RPC**, awaits a zero-delay timer, and sets
`Content-Encoding: gzip` on its outgoing Response. Cloudflare's runtime is asked
to compress that body; the example is not forwarding already compressed bytes.
The client can receive **HTTP 200 with only a gzip header or an empty body**.

Reproduced consistently in local workerd `1.20260906.1` (pinned here) and
`1.20260907.1`, and intermittently on deployed Workers on September 7, 2026. Removing the Worker's gzip header preserved
the body in every tested case, but **RPC stream exceptions still occurred**.
Wrapping the response without gzip did not eliminate those exceptions.

## Reproduce locally

Requires Node.js 22+ and pnpm 11.7.0. Tested on macOS ARM64 with Node 24.13.1.
Dependencies are pinned. No Cloudflare account, credentials, or deployment needed.

In one terminal, from this directory:

```sh
pnpm install --frozen-lockfile
pnpm workerd:serve
```

In another terminal:

```sh
pnpm workerd:probe
```

**Expected on the pinned runtime: exit 1, one failing body assertion and five
passing controls.** These tests assert correct behavior, so reproducing the bug
fails the test. The failure is strict gzip decompression of an incomplete stream.

The server listens on `127.0.0.1:8080`; stop it with Ctrl-C. If that port is taken,
choose an unused port and use it for both commands:

```sh
pnpm workerd:serve -s http=127.0.0.1:8081
REPRO_URL=http://127.0.0.1:8081 pnpm workerd:probe
```

The server and probe still run in separate terminals. Each probe request uses a
fresh connection and a 10-second deadline. Startup or connection failures are not
proof of the bug. A passing body assertion does not check runtime logs.

## What the example does

The [callee](workerd/callee.mjs) returns `Response.json({ ok: true })`.
The affected path in the [caller](workerd/caller.mjs) is:

```js
let response = await env.CALLEE.getJson();
await new Promise(resolve => setTimeout(resolve, 0));
response = new Response(response.body, response);
response.headers.set("Content-Encoding", "gzip");
return response;
```

Cloudflare documents [Responses and streams over RPC](https://developers.cloudflare.com/workers/runtime-apis/rpc/#readablestream-writeablestream-request-and-response),
with ownership transferred to the receiver, and [automatic Response encoding](https://developers.cloudflare.com/workers/runtime-apis/response/#parameters)
according to `Content-Encoding`. The example does not read, cancel, or dispose the
received body. The timer yields to the event loop. In the tested variants,
`await Promise.resolve()` did not trigger the failure; the additional await alone
is not a sufficient description of the trigger.

| Route | RPC | Additional await | Wrap Response | Set gzip header | Local body |
| --- | --- | --- | --- | --- | --- |
| `/` | Yes | Timer | Yes | Yes | Truncated |
| `/no-await` | Yes | None | Yes | Yes | Complete |
| `/microtask` | Yes | `Promise.resolve()` | Yes | Yes | Complete |
| `/no-gzip` | Yes | Timer | No | No | Complete |
| `/wrapped-no-gzip` | Yes | Timer | Yes | No | Complete |
| `/no-rpc` | No | Timer | Yes | Yes | Complete |

Compare `/no-gzip` with `/wrapped-no-gzip` to isolate wrapping. Compare
`/wrapped-no-gzip` with `/` to isolate setting the gzip header. `/microtask`
keeps an additional await while replacing the timer with an already resolved
promise.

Locally, the failed response contains just these 10 bytes:

```text
1f8b0800000000000013
```

That is a gzip header without payload or trailer. A complete gzip response here
is 31 bytes and decodes to the 11-byte JSON `{"ok":true}`. Node's `fetch` exposed
the truncated response as an empty body; the probe preserves bytes and uses a
strict decoder. workerd also reports:

```text
ReadableStream received over RPC disconnected prematurely.
```

Both no-gzip variants also logged that error despite complete bodies.
A [likely mechanism in the RPC stream completion bookkeeping](evidence/source-analysis.md)
fits both symptoms. That analysis is based on source inspection and runtime
comparisons; a patched runtime has not been built or tested here.

A [report draft](REPORT.md) collects the reproduction, observed impact, and this
possible explanation for upstream review.

## Deployed result

The primary deployed comparison used 200 requests through SJC: 10 per route,
client encoding (`gzip` or `identity`), and compatibility date (`2026-04-09` or
`2026-09-06`). Every HTTP status was 200. This comparison predates the
`/microtask` route; that control has only been tested locally here.

| Route | Complete bodies | Captured Worker outcomes |
| --- | --- | --- |
| `/` | 27/40 | 26 `ok`, 12 `exception`, 2 unmatched |
| `/no-await` | 40/40 | 38 `ok`, 2 unmatched |
| `/no-gzip` | 40/40 | 37 `exception`, 3 unmatched |
| `/wrapped-no-gzip` | 40/40 | 37 `exception`, 1 `ok`, 2 unmatched |
| `/no-rpc` | 40/40 | 40 `ok` |

Six responses contained only a gzip header; seven identity responses were empty.
The Worker set the gzip header in all 13 failed cases. **Client
`Accept-Encoding: identity` alone did not reliably prevent deployed body loss.**
All 12 failed requests with captured tail events had the stream error above;
one failed request had no captured event.

These are observations for this payload, region, and configuration, not estimates
of general production failure rates. Compatibility dates do not identify the
managed runtime binary. The [evidence and history](evidence/README.md) preserve
version comparisons, raw-byte samples, missing logs, and the earlier all-pass run.
All temporary Workers used for these experiments were deleted.

To repeat the full matrix and correlate Worker logs, follow the
[deployed experiment instructions](scripts/README.md). The included runner uses
Python 3 and curl; the simple Node probe above needs neither.

## Wrangler reproduction and workarounds

The separate Wrangler harness shows how application code can trigger the failure
without explicitly setting gzip or adding a timer. Wrangler's request-body-draining
middleware adds an await for non-GET responses, and Miniflare supplies compression.
The fixture [gateway](src/gateway.mjs) simply forwards the RPC Response.

```sh
unset WRANGLER_DISABLE_REQUEST_BODY_DRAINING
pnpm test
```

On pinned Wrangler `4.129.0`: **exit 1, one failure and three passing controls**.
The harness owns its server on an allocated port and stops it after testing.

| Wrangler case | Observed body |
| --- | --- |
| `POST /direct` with gzip | Empty |
| `GET /direct` with gzip | Complete |
| `POST /direct` with client identity encoding | Complete |
| `POST /materialized` buffers the RPC body before forwarding | Complete |

`WRANGLER_DISABLE_REQUEST_BODY_DRAINING=1 pnpm test` passes all four assertions.
This is a local middleware workaround; it does not fix code that explicitly awaits
before returning the RPC Response.

Omitting the Worker's gzip header preserved bodies in the pure example, but left
stream exceptions. Buffering with `await response.arrayBuffer()` preserved bodies
in the local Wrangler test, at the cost of streaming and memory use; it has not
been validated here as a deployed workaround. An older tested workerd version,
`1.20260804.1`, passed the original controls. See the [version history](evidence/README.md#versions-and-evidence)
for the regression bounds.
