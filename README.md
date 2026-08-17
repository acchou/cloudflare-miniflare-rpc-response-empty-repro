# workerd drops a delayed RPC `Response` body during gzip egress

This repository reproduces a workerd regression in which a `Response` received
over JavaScript RPC reaches the client as `200 OK` with an empty body.

The failure requires all three conditions:

1. a Worker receives a `Response` from a `WorkerEntrypoint` over RPC;
2. the caller crosses an asynchronous boundary before returning that response; and
3. workerd applies gzip compression during response egress.

Removing any one of those conditions preserves the body. Cloudflare documents
`Response` as a supported RPC value whose body stream transfers to the recipient,
so returning the response after another `await` is expected to work.

## Wrangler and Miniflare reproduction

Requirements: Node.js 22 or newer and pnpm 11.

```sh
pnpm install
pnpm test
```

With an affected workerd build, the suite reports one expected failure and three
passing controls:

- `POST /direct` with the default `Accept-Encoding` returns gzip with an empty body;
- `GET /direct` returns the same RPC response body;
- `POST /direct` with `Accept-Encoding: identity` returns the body; and
- materializing the RPC response before returning it from `POST` returns the body.

The first test expresses the documented behavior, so it intentionally fails while
the regression is present rather than asserting the empty body as correct.

### Why `POST` exposes the failure locally

`POST` is an indirect trigger, not the underlying condition. During local
development, Wrangler injects `middleware-ensure-req-body-drained`, which awaits
the unused request body in a `finally` block. Miniflare's entry Worker then selects
gzip for compressible responses when the client accepts it. Those two development
layers supply the asynchronous boundary and gzip conditions required by the
workerd defect.

Setting the environment variable below disables the first condition supplied by
Wrangler and makes all four tests pass:

```sh
WRANGLER_DISABLE_REQUEST_BODY_DRAINING=1 pnpm test
```

## Pure workerd reproduction

The files under [`workerd/`](workerd/) reproduce the runtime defect without
Wrangler or Miniflare. Start workerd in one terminal:

```sh
pnpm workerd:serve
```

Then run the probe in another:

```sh
pnpm workerd:probe
```

An affected build prints:

```text
affected: await + gzip: status=200 encoding=gzip body=""
control: no await: status=200 encoding=gzip body="{\"ok\":true}"
control: no gzip: status=200 encoding=identity body="{\"ok\":true}"
```

workerd logs the underlying error:

```text
workerd/io/external-pusher.c++:76: failed: remote.jsg.Error: ReadableStream received over RPC disconnected prematurely.
```

## Regression range

| Package | Version | Result |
| --- | --- | --- |
| workerd | `1.20260804.1` | last good tested |
| workerd | `1.20260807.2` | first bad tested |
| workerd | `1.20260817.1` | still affected |
| Wrangler | `4.121.0` | passes; bundles workerd `1.20260804.1` |
| Wrangler | `4.122.0` | fails; bundles workerd `1.20260811.1` |
| Wrangler | `4.123.0` | fails; bundles workerd `1.20260811.1` |

The compatibility date does not control the failure. It reproduces with the
`2026-04-09` date in this repository and with older tested dates.

The original application route preserves the complete response body in the
deployed Workers runtime. This repository isolates the local runtime regression.

## Workarounds

- Pin Wrangler to `4.121.0`, or point `MINIFLARE_WORKERD_PATH` to workerd
  `1.20260804.1`.
- Materialize the RPC response body before returning it, as the
  `/materialized` control does.
- For local tests, send `Accept-Encoding: identity`.
- For local development, set `WRANGLER_DISABLE_REQUEST_BODY_DRAINING=1`. This
  disables Wrangler's workaround for unused request bodies and should not be
  treated as a production fix.
- Setting `Content-Encoding: identity` on the response also avoids the affected
  gzip path, but changes compression behavior.

## Related documentation and reports

No exact public report was found in the workerd or workers-sdk trackers as of
August 17, 2026. These reports cover adjacent compression and stream-lifecycle
behavior:

- [Cloudflare RPC Request and Response documentation](https://developers.cloudflare.com/workers/runtime-apis/rpc/#readablestream-writablestream-request-and-response)
- [Cloudflare RPC lifecycle documentation](https://developers.cloudflare.com/workers/runtime-apis/rpc/lifecycle/)
- [workers-sdk #8004: Miniflare aggressively buffers responses](https://github.com/cloudflare/workers-sdk/issues/8004)
- [workers-sdk #15203: request-body stream failure in local development](https://github.com/cloudflare/workers-sdk/issues/15203)
- [workerd #2588: RPC request-stream lifecycle behavior](https://github.com/cloudflare/workerd/issues/2588)
