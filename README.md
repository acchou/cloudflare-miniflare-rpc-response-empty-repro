# workerd truncates an RPC Response during gzip compression

A Worker receives a `Response` over RPC, waits once, and returns it with gzip
compression. The client receives **HTTP 200 with an incomplete gzip stream**.
In Node's `fetch`, this appears as an empty body. A strict gzip decoder rejects it.

**Still reproduced locally on September 6, 2026:** workerd `1.20260906.1` and
Wrangler `4.129.0`. This repo pins those versions. Local tests need no Cloudflare
account, credentials, deployment, or application dependencies.

**Also reproduced intermittently on deployed Workers on September 7, 2026.**
A follow-up experiment captured HTTP 200 with only a gzip header, as well as
empty identity responses. Both wrapped and original no-gzip Responses returned
complete bodies but logged stream exceptions. See
[deployed results](#deployed-workers-experiment).

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
the local pure-workerd example below fails on GET with an explicit timer await.

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

**Expected while affected: exit 1, one failed test and four passing controls.**
The probe checks HTTP status, encoding, strict decompression, and the exact JSON
value for every route. It has a 10-second request deadline. Exit 0 means all five
cases passed; a startup, connection, or control failure is not evidence of this bug.
Each case uses a fresh connection so the broken response cannot contaminate the
following controls through connection reuse.

| Route | RPC | Timer await | Wrap Response | Set gzip header | Expected body while affected |
| --- | --- | --- | --- | --- | --- |
| `/` | Yes | Yes | Yes | Yes | Fails |
| `/no-await` | Yes | No | Yes | Yes | Passes |
| `/no-gzip` | Yes | Yes | No | No | Passes |
| `/wrapped-no-gzip` | Yes | Yes | Yes | No | Passes |
| `/no-rpc` | No | Yes | Yes | Yes | Passes |

`/no-gzip` preserves the original control, which skips both wrapping and gzip.
`/wrapped-no-gzip` adds `new Response(response.body, response)` without setting
`Content-Encoding`. Comparing these two isolates wrapping; comparing the wrapped
control with `/` isolates setting the gzip header.

On September 7, both no-gzip variants returned complete bodies on 20/20 local
requests each (10 per compatibility date). Each also logged 20 stream errors.
Wrapping alone did not change either result. Each route ran in its own workerd
process to attribute the log count. The five-case Node probe had one failure and
four passing controls.

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
strict byte validation, the then-four-case probe was run three times against
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

The **truncated-body failure is reproduced locally and intermittently on deployed
Workers**. Stream exceptions also occur with complete bodies on both no-gzip
variants. The matching error text and truncated gzip symptom support a related
stream failure, but do not identify the exact root cause. Targeted upstream
searches on September 6 found no exact report.

## Deployed Workers experiment

Tested September 7, 2026 through the SJC edge, using isolated Workers with no
storage, application bindings, or secrets. They ran the repo's caller and callee.
Both compatibility dates `2026-04-09` and `2026-09-06` were tested, without
compatibility flags. Cloudflare manages the deployed runtime build; compatibility
dates and deployment IDs do not identify a workerd binary.

### Wrapping comparison and deployed truncation

The original `/no-gzip` control skipped both wrapping and compression. The new
`/wrapped-no-gzip` control isolates wrapping while keeping RPC, the timer await,
and the absence of the gzip header identical. **Wrapping alone did not prevent
stream errors or change the complete client body in these tests.**

The primary follow-up comparison comprises 200 requests: 10 per route, client
`Accept-Encoding` (`gzip` or `identity`), and compatibility date. A separate
Worker pair for the second date avoided mixing deployments during propagation.
curl preserved wire bytes; strict gzip decoding and exact body comparison checked
for `{"ok":true}`. Of 200 requests, 191 were matched by CF-Ray to tail events with
the expected caller deployment ID; nine events were not captured.

| Route | Complete HTTP 200 bodies | Worker outcomes |
| --- | --- | --- |
| `/` (RPC + await + wrap + gzip header) | 27/40 | 26 `ok`, 12 `exception`, 2 unmatched |
| `/no-await` | 40/40 | 38 `ok`, 2 unmatched |
| `/no-gzip` (original Response) | 40/40 | 37 `exception`, 3 unmatched |
| `/wrapped-no-gzip` | 40/40 | 37 `exception`, 1 `ok`, 2 unmatched |
| `/no-rpc` | 40/40 | 40 `ok` |

Every response had HTTP status 200. On `/`, six gzip requests returned only the
10-byte gzip header `1f8b0800000000000003`; seven identity requests returned zero
bytes. Four failures occurred with the April date and nine with the September
date. All 12 failures with a captured tail event reported:

```text
ReadableStream received over RPC disconnected prematurely.
```

The thirteenth failure had no captured tail event. Both no-gzip variants always
returned the complete 11-byte JSON body, for both client encodings. Their captured
exceptions had the same message. The one `ok` wrapped invocation does not establish
a reliable improvement; exceptions persisted with wrapping on both dates.

**The local truncation symptom now also has a deployed reproduction.** Setting
`Content-Encoding: gzip` on the Worker Response was still part of every truncated
case, including those where the client requested identity. Client identity alone
therefore did not reliably avoid this deployed failure. The exact runtime cause
and the relationship to exceptions accompanying complete bodies remain unresolved.
These results cover this payload, code, dates, and region; the observed failure
fractions are not estimates of a general production failure rate.

[Wrapping experiment evidence](evidence/wrapping-2026-09-07.json) records local
results, deployment IDs, per-case counts, raw-byte samples, failed responses, and
correlated outcomes. It also retains two exploratory batches outside the primary
table: an initial 100 requests lost raw metadata for three failed gzip decodes,
and another 100 crossed a deployment transition (five events used the earlier
caller version). Missing evidence is marked explicitly. All temporary Workers
were deleted after testing.

### Earlier baseline

An earlier experiment on September 7 UTC (September 6 Pacific) returned intact
bodies on all 160 requests and exceptions on all 40 `/no-gzip` invocations. That
observation is preserved in the [original evidence](evidence/deployed-2026-09-07.json).
It did not include the wrapped no-gzip control. The later captured truncations
supersede the earlier assessment that this symptom had only reproduced locally.
The experiments do not establish why the failure frequency changed.

### Repeat on your Cloudflare account

Deployment requires authentication. Choose unused names in the two configs below
and update the caller's `CALLEE` service reference to match. The callee has no
public URL; only the caller is exposed on workers.dev.

```sh
pnpm exec wrangler whoami
pnpm exec wrangler deploy --config workerd/wrangler.callee.jsonc
pnpm exec wrangler deploy --config workerd/wrangler.caller.jsonc
```

Start a tail before probing, using the caller name from its config:

```sh
pnpm exec wrangler tail rpc-gzip-repro-caller --format json
```

In another terminal, use the HTTPS URL printed by deployment:

```sh
REPRO_URL=https://YOUR-CALLER.YOUR-SUBDOMAIN.workers.dev pnpm workerd:probe
```

The probe now has five cases. The `/` assertion can fail intermittently on
deployed Workers; a single all-pass run does not rule out the bug. Both no-gzip
cases can report stream exceptions despite passing the client assertion. Keep
the tail output alongside response bytes. The probe explicitly requests gzip;
to inspect the identity behavior on the affected route:

```sh
curl --http1.1 -i -H 'Accept-Encoding: identity' \
  https://YOUR-CALLER.YOUR-SUBDOMAIN.workers.dev/
```

To compare dates, use a separately named Worker pair with the other date and
update its service binding. Reusing names can briefly mix old and new deployments;
check the tail version IDs before attributing results to a date. Remove each
caller first, then its RPC dependency, when finished:

```sh
pnpm exec wrangler delete --config workerd/wrangler.caller.jsonc
pnpm exec wrangler delete --config workerd/wrangler.callee.jsonc
```

## Workarounds and tradeoffs

- **Wrangler development:** disable request-body draining as shown above. This
  avoids the middleware's extra await; it does not fix application code that
  explicitly awaits before returning an RPC Response.
- **Buffer the body:** read `await response.arrayBuffer()` before returning a new
  Response, as the `/materialized` control does. This sacrifices streaming and
  increases memory use for large responses.
- **Avoid gzip on this path:** use `Accept-Encoding: identity` in the Wrangler
  client, or omit the gzip header in the pure example. Both no-gzip variants
  delivered complete bodies in these tests but still logged stream exceptions.
  Client identity alone did not reliably avoid deployed truncation when the
  Worker still set the gzip header.
- **Older runtime:** `1.20260804.1` is a known passing comparison. Downgrading is
  useful for regression diagnosis, not a general recommendation to stay on old tooling.
