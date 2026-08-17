# Miniflare RPC `Response` body lost on POST

This repository reproduces a local Miniflare bug with two Workers and a 12-byte
JSON response:

1. the gateway handles an incoming `POST` request;
2. it calls a `WorkerEntrypoint` method over a service binding;
3. that method returns `Response.json({ ok: true })`; and
4. the gateway directly returns the RPC `Response`.

Cloudflare documents `Response` as a supported RPC value whose body is streamed to
the recipient. Wrangler's local test harness instead returns `200 OK` with the
`application/json` content type and an empty body for the `POST` route.

## Run

Verified with Node.js 22.22.0 and pnpm 11.7.0 on macOS.

```sh
pnpm install
pnpm test
```

The first test is expected to fail:

```text
Expected values to be strictly equal:

expected the RPC Response body to be non-empty
```

The controls show that both the HTTP method and direct forwarding matter:

- directly forwarding the same RPC `Response` for `GET` preserves the body;
- reading and reconstructing the RPC `Response` before returning it for `POST`
  preserves the body.

The failing path is equivalent to:

```js
async fetch(request, env) {
  return await env.ACCOUNT.createAuthToken();
}
```

where `createAuthToken()` returns `Response.json({ ok: true })` from a
`WorkerEntrypoint`.

## Versions

- Wrangler `4.123.0`
- Miniflare `5.20260811.1-alpha` (resolved by Wrangler)
- workerd `1.20260811.1` (resolved by Wrangler)

The bug also reproduces when `MINIFLARE_WORKERD_PATH` points to workerd
`1.20260817.1`, so updating workerd alone does not resolve it. The original
application route returns the complete body in the deployed Workers runtime; this
repository isolates the local `createTestHarness()` failure.

## Relevant documentation

- <https://developers.cloudflare.com/workers/runtime-apis/rpc/#readablestream-writablestream-request-and-response>
- <https://developers.cloudflare.com/workers/testing/integration-testing/>
