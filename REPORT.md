# 🐛 Bug Report — Runtime APIs: RPC Response stream errors and truncated HTTP 200 bodies

Thanks for taking a look. I've put together a small reproducer for an apparent
regression when returning a Response received over RPC. Locally, a timer await
followed by outgoing gzip encoding produces HTTP 200 with an incomplete body.
I also reproduced intermittent body loss on deployed Workers, and stream
exceptions on uncompressed responses whose bodies arrived intact.

## Environment

- macOS ARM64; Node.js 24.13.1; pnpm 11.7.0.
- Pure workerd: `1.20260804.1` passes; `1.20260807.2`, `1.20260906.1`, and
  `1.20260907.1` reproduce the body failure. The repo pins `1.20260906.1`.
- A separate Wrangler `4.129.0` example also reproduces the failure.
- Compatibility dates tested: `2026-04-09` and `2026-09-06`, without flags.
- Deployed requests reached SJC. The managed runtime build is unknown.

## Minimal reproduction

[Repository](https://github.com/acchou/cloudflare-miniflare-rpc-response-empty-repro)

```sh
git clone https://github.com/acchou/cloudflare-miniflare-rpc-response-empty-repro.git
cd cloudflare-miniflare-rpc-response-empty-repro
pnpm install --frozen-lockfile
pnpm workerd:serve
```

In a second terminal, from the same directory:

```sh
pnpm workerd:probe
```

The server uses `127.0.0.1:8080`; the README explains how to choose another port.
No account, credentials, storage, or external service is needed for this local
reproduction. Stop the server with Ctrl-C when finished.

The callee returns `Response.json({ ok: true })`. The affected caller path is:

```js
let response = await env.CALLEE.getJson();
await new Promise(resolve => setTimeout(resolve, 0));
response = new Response(response.body, response);
response.headers.set("Content-Encoding", "gzip");
return response;
```

This receives plain JSON over RPC and asks the runtime to compress the outgoing
response. My understanding of the [RPC](https://developers.cloudflare.com/workers/runtime-apis/rpc/#readablestream-writeablestream-request-and-response)
and [Response encoding](https://developers.cloudflare.com/workers/runtime-apis/response/#parameters)
documentation is that this should preserve the full body. The example does not
read, cancel, or dispose the received stream before returning it.

## Expected and observed behavior

Expected: the complete `{"ok":true}` body and normal stream completion.

On the pinned runtime, the probe exits 1: the affected route fails strict gzip
decoding, while five controls pass their body assertions. The failed response is
HTTP 200 with only the 10-byte gzip header `1f8b0800000000000013`. The runtime logs:

```text
ReadableStream received over RPC disconnected prematurely.
```

Removing the timer, or replacing it with `await Promise.resolve()`, preserves the
body and avoids the exception in local tests. Both no-gzip controls return complete
bodies but still log the exception, whether the Response is wrapped or returned
directly. Removing RPC avoids both symptoms in these tests.

The [deployed comparison](https://github.com/acchou/cloudflare-miniflare-rpc-response-empty-repro/blob/main/evidence/README.md#deployed-workers-experiment)
captured 13 incomplete bodies out of 40 requests to the affected route: six
header-only gzip responses and seven empty identity responses. The Worker set its
gzip header in all 13 cases. Both no-gzip routes returned complete bodies on 40/40
requests each, with 37 captured stream exceptions each. Nine of the full matrix's
200 requests lacked a matching tail event; the evidence marks those gaps.
The deployed matrix predates the new microtask control, which is only locally tested.

## Possible explanation

Source inspection suggests a completion-bookkeeping interaction, though I have
not built a patched runtime to verify it. In `external-pusher.c++`, the receiving
adapter updates its remaining length and ended state in `tryRead`, while `pumpTo`
delegates without that update. Commit
[`1d11016af`](https://github.com/cloudflare/workerd/commit/1d11016af66a6165dadfd541408343fa554214d3)
adds an EOF-check read after pumping the advertised length, before gzip finalization.
It falls within the tested regression window and was intended to address a cache
pump deadlock.

This looks like a possible explanation for both symptoms. The
[source analysis](https://github.com/acchou/cloudflare-miniflare-rpc-response-empty-repro/blob/main/evidence/source-analysis.md)
has pinned links and the await/runtime comparisons. I'd appreciate a check of
that interpretation, particularly whether completion bookkeeping belongs in the
adapter's pump path. Any change would also need to preserve the original cache
fix and detection of genuinely incomplete streams.
