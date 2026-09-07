# Repeat the deployed experiment

The Workers are the same [caller](../workerd/caller.mjs) and
[callee](../workerd/callee.mjs) used locally. The callee's `fetch` handler returns
404 because deployment requires an event handler; its RPC method returns JSON.

Run these commands from the repository root after installing its dependencies.
The experiment scripts require Python 3.9+ and curl on PATH, with no Python packages.

## Deploy an isolated pair

Choose unused names in `workerd/wrangler.callee.jsonc` and
`workerd/wrangler.caller.jsonc`. Update the caller's `CALLEE` service reference to
match. Set the same compatibility date on both configs. The callee has no public
URL, storage, or secrets; the caller is exposed on workers.dev.

```sh
pnpm exec wrangler whoami
pnpm exec wrangler deploy --config workerd/wrangler.callee.jsonc
pnpm exec wrangler deploy --config workerd/wrangler.caller.jsonc
```

Record the caller URL and both `Current Version ID` values printed by deployment.
Start a tail in another terminal, replacing the name if you changed it:

```sh
mkdir -p runs
pnpm exec wrangler tail rpc-gzip-repro-caller --format json > runs/tail.json
```

Keep it running throughout the matrix. Raw tails can contain client IPs and request
headers; `runs/` is ignored by Git. The correlation output includes only selected
response fields, CF-Ray IDs, deployment IDs, outcomes, and exception messages.

## Run the matrix

Replace the URL and version placeholder below. The date must match both configs.

```sh
mkdir -p runs
python3 scripts/matrix.py \
  https://YOUR-CALLER.YOUR-SUBDOMAIN.workers.dev runs/matrix.json \
  --compatibility-date 2026-04-09 \
  --callee-version CALLEE_VERSION_ID \
  --repetitions 10
```

This sends **120 sequential requests**, with no retries: six routes × two client
encodings × ten repetitions. Each request starts a fresh curl process, uses
HTTP/1.1, and has a 20-second deadline. It preserves raw bytes before strict gzip
decoding, including partial responses on transport or decoding failure. It checks
HTTP 200 and the exact body `{"ok":true}`, and records the actual encoding.

The JSON is checkpointed after every round. Exit 0 means all client bodies passed;
exit 1 means at least one did not. **A passing run does not establish that the
intermittent bug is fixed, or that Worker logs are free of exceptions.**

After the requests finish, allow the tail to receive the final events, then stop
it with Ctrl-C. Run the matcher using the recorded caller version:

```sh
python3 scripts/correlate.py runs/matrix.json runs/tail.json runs/report.json \
  --caller-version CALLER_VERSION_ID
```

The matcher accepts Wrangler's pretty-printed JSON and CLI startup banners. It
matches response CF-Ray IDs to request IDs in the tail, checking the caller
version on each captured event. The report retains every response and shows:

- Body success separately from the Worker outcome and exceptions.
- Missing or ambiguous tail matches, and unexpected caller versions.
- Incomplete or malformed tail JSON, without silently declaring complete capture.

Exit 0 requires complete matching, the expected caller version, intact bodies,
and `ok` outcomes without exceptions. Exit 1 flags any observed failure or capture
gap; inspect the report to distinguish them. The compatibility date and callee
version are operator-supplied metadata. The matcher verifies the **caller version
in the tail**, not remote settings or the deployed workerd binary.

## Compare dates and clean up

For a second date, use a separately named Worker pair and a new output directory.
Set both configs to that date, update the caller's service binding, and repeat.
Using separate names avoids mixing old and new deployments during propagation.
Keep each pair's configs until cleanup, or delete that pair before editing them.

Delete each caller before its RPC dependency:

```sh
pnpm exec wrangler delete --config workerd/wrangler.caller.jsonc
pnpm exec wrangler delete --config workerd/wrangler.callee.jsonc
```

For published historical runs, see [evidence](../evidence/README.md). These scripts
generalize the curl matrix and CF-Ray matching used there; they do not rerun or
replace those recorded results. The historical deployed runs used five routes;
the added `/microtask` control has only been tested locally here. The matcher also
accepts their older raw matrix format, which kept CF-Ray inside `headers`.

To check the experiment tools themselves:

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
```
