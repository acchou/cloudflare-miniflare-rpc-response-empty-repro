"""Repeat the response matrix with curl, preserving bytes before decoding."""

import argparse
import collections
import datetime
import gzip
import json
from pathlib import Path
import subprocess
import tempfile
import zlib
from urllib.parse import urlsplit

ROUTES = ["/", "/no-await", "/microtask", "/no-gzip", "/wrapped-no-gzip", "/no-rpc"]


def request(url, route, accept):
    row = {
        "route": route,
        "acceptEncoding": accept,
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "passed": False,
    }
    with tempfile.TemporaryDirectory() as scratch:
        headers_path = Path(scratch) / "headers"
        body_path = Path(scratch) / "body"
        result = subprocess.run([
            "curl", "--http1.1", "--silent", "--show-error", "--max-time", "20",
            "-H", "Accept-Encoding: " + accept, "-H", "Cache-Control: no-cache",
            "-D", str(headers_path), "-o", str(body_path), "-w", "%{http_code}",
            url.rstrip("/") + route,
        ], capture_output=True, text=True)
        # Do not use --compressed: the saved bytes must remain as received.
        headers = {}
        for line in headers_path.read_text().splitlines() if headers_path.exists() else []:
            if line.startswith("HTTP/"):
                headers = {}
            elif ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower()] = value.strip()
        raw = body_path.read_bytes() if body_path.exists() else b""
        encoding = headers.get("content-encoding")
        row.update(
            status=int(result.stdout) if result.stdout.strip().isdigit() else None,
            encoding=encoding,
            cfRay=headers.get("cf-ray"),
            wireBytes=len(raw),
            wireHex=raw.hex(),
            curlExitCode=result.returncode,
        )
        # Preserve partial bodies even when curl or strict decompression fails.
        if result.returncode:
            row["transportError"] = result.stderr.strip()
        try:
            if encoding not in (None, "identity", "gzip"):
                raise ValueError("Unexpected content encoding: " + encoding)
            body = gzip.decompress(raw) if encoding == "gzip" else raw
            row["body"] = body.decode("utf-8")
            row["passed"] = result.returncode == 0 and row["status"] == 200 and body == b'{"ok":true}'
        except (OSError, EOFError, UnicodeError, ValueError, zlib.error) as error:
            row["decodeError"] = str(error)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Caller origin, such as https://NAME.SUBDOMAIN.workers.dev")
    parser.add_argument("output", type=Path)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--compatibility-date", required=True, help="Declared date on BOTH Worker configs")
    parser.add_argument("--callee-version", required=True, help="Callee version ID printed by deployment")
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme not in ("http", "https") or not url.netloc or url.path not in ("", "/") or url.query or url.fragment or url.username:
        parser.error("url must be an HTTP(S) origin without a path, credentials, query, or fragment")
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    # The date and callee version are operator-supplied labels, not remote checks.
    data = {"compatibilityDate": args.compatibility_date, "calleeVersion": args.callee_version, "rows": []}
    for iteration in range(args.repetitions):
        for accept in ("gzip", "identity"):
            for route in ROUTES:
                row = request(args.url, route, accept)
                row["iteration"] = iteration
                data["rows"].append(row)
        # Checkpoint every round so interrupted runs retain completed rounds.
        args.output.write_text(json.dumps(data, indent=2) + "\n")
        print(f"Completed {iteration + 1}/{args.repetitions} rounds", flush=True)
    for accept in ("gzip", "identity"):
        for route in ROUTES:
            rows = [r for r in data["rows"] if r["route"] == route and r["acceptEncoding"] == accept]
            print(accept, route, "intact", sum(r["passed"] for r in rows), "/", len(rows),
                  "wire bytes", dict(collections.Counter(r["wireBytes"] for r in rows)))
    return 0 if all(row["passed"] for row in data["rows"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
