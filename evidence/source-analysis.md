# Likely RPC stream completion mechanism

This is a possible explanation for the recorded behavior, offered as a starting
point for investigation. The source observations below are directly inspectable;
the proposed causal chain and fix have not been validated with a patched workerd
build. In particular, the exact deployed runtime build is unknown.

## Source observations

In pinned workerd `v1.20260906.1`:

1. [`ExplicitEndInputPipeAdapter::tryRead`](https://github.com/cloudflare/workerd/blob/v1.20260906.1/src/workerd/io/external-pusher.c%2B%2B#L74-L95)
   subtracts bytes from `expectedLength` and marks the stream ended when that
   count reaches zero. A short read without a recognized end throws
   `ReadableStream received over RPC disconnected prematurely.`
2. The adapter's [`pumpTo`](https://github.com/cloudflare/workerd/blob/v1.20260906.1/src/workerd/io/external-pusher.c%2B%2B#L120-L131)
   delegates directly to the inner stream, without updating that bookkeeping.
   The adapter is constructed around a pipe with the advertised length, and
   [`unwrapStream`](https://github.com/cloudflare/workerd/blob/v1.20260906.1/src/workerd/io/external-pusher.c%2B%2B#L143-L175)
   returns a promised stream.
3. [`EncodedAsyncOutputStream::tryPumpFrom`](https://github.com/cloudflare/workerd/blob/v1.20260906.1/src/workerd/api/system-streams.c%2B%2B#L330-L368)
   pumps the advertised length, when available, then reads one byte to check EOF.
   Gzip finalization is chained after that operation succeeds.

The extra EOF read was added in commit
[`1d11016af`, “Pump advertised length then verify EOF in tryPumpFrom”](https://github.com/cloudflare/workerd/commit/1d11016af66a6165dadfd541408343fa554214d3).
Its stated purpose was to fix a `cache.put()` deadlock while preserving clean
producer completion. It reached public main in
[PR #6909](https://github.com/cloudflare/workerd/pull/6909), merged August 4, 2026
at 16:45 UTC, after the last-good `v1.20260804.1` release was published at 01:01 UTC.
This makes it a candidate within the observed regression window, rather than a
commit proven responsible by a parent/child build comparison.

## How this could explain the results

If a pump consumes the advertised bytes without updating the receiving adapter's
remaining-length count, the subsequent EOF check can encounter stale completion
state and report a premature disconnect. With an uncompressed output, the bytes
may already have reached the client. With gzip, the exception can prevent
finalization; our small payload then produces only the gzip header.

The promised-stream layer also provides a plausible connection to timing: the
availability of the underlying stream and its length may differ before and after
the event loop runs. The experiments below establish the observed await
sensitivity; they do not instrument the exact promise-resolution order.

## Independent local checks on September 7

Each runtime/route combination was requested five times in its own workerd
process, using compatibility date `2026-04-09` and client `Accept-Encoding: gzip`.
The checked binaries were the published macOS ARM64 builds.

| Runtime | Affected `/` body | Routes logging the stream error |
| --- | --- | --- |
| `1.20260804.1` | Complete, 5/5 | None |
| `1.20260906.1` | Truncated, 5/5 | `/`, `/no-gzip`, `/wrapped-no-gzip` |
| `1.20260907.1` | Truncated, 5/5 | Same three |

Separate variants on `1.20260907.1` changed only the additional await on `/`:

| Additional await | Truncated bodies | Stream errors |
| --- | --- | --- |
| None | 0/5 | 0/5 |
| One `await Promise.resolve()` | 0/5 | 0/5 |
| Ten `await Promise.resolve()` calls | 0/5 | 0/5 |
| `await new Promise(resolve => setTimeout(resolve, 0))` | 5/5 | 5/5 |
| `await scheduler.wait(0)` | 5/5 | 5/5 |

[Recorded results](await-2026-09-07.json) retain wire-byte samples and error counts.
The repository now includes `/microtask` for the one-resolved-promise control.
A subsequent six-route check returned complete bodies without exceptions on that
route for all three runtimes (5/5 each). The Node probe passed all six cases on
`1.20260804.1`; it had one failing body assertion and five passing controls on
both September builds. These additional checks are in the same evidence file.
These checks are local; the historical deployed matrix did not include that route.
The no-gzip controls returning intact bodies does not prove that all consequences
of their stream exceptions are harmless.

## Possible next check

Updating the adapter's remaining-length and completion state after a successful
pump, consistently with its read methods, looks worth testing. A runtime test
should cover complete and genuinely short streams, partial pumps, and the
`cache.put()` case that motivated the original change. This repository does not
include a proposed runtime patch or claim that a fix is validated.

As checked on September 7, today's published `1.20260907.1` still reproduces the
failure. Public main differed from the pinned tag only in release metadata, and
targeted issue/PR searches found no matching report or fix. This does not establish
Cloudflare's internal awareness, investigation status, or deployment plans.
