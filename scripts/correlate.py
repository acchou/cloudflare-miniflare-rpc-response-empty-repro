"""Match matrix responses to Wrangler JSON tail events using CF-Ray."""

import argparse
import collections
import json
from pathlib import Path


def parse_tail(text):
    """Accept pretty-printed JSON objects and CLI banners; flag incomplete JSON."""
    decoder = json.JSONDecoder()
    events = []
    while text.strip():
        text = text.lstrip()
        if not text.startswith("{"):
            # pnpm/Wrangler can print human-readable startup banners.
            _, _, text = text.partition("\n")
            continue
        try:
            event, end = decoder.raw_decode(text)
        except ValueError as error:
            return events, str(error)
        events.append(event)
        text = text[end:]
    return events, None


def correlate(data, events, expected_version, parse_error=None):
    by_ray = collections.defaultdict(list)
    for event in events:
        headers = event.get("event", {}).get("request", {}).get("headers", {})
        ray = next((value for key, value in headers.items() if key.lower() == "cf-ray"), None)
        if ray:
            by_ray[ray.split("-")[0].lower()].append(event)
    rows = []
    for source in data["rows"]:
        # Only export response evidence; never copy URLs, IPs, or raw tail headers.
        row = {key: source[key] for key in (
            "iteration", "route", "acceptEncoding", "time", "status", "encoding",
            "wireBytes", "wireHex", "body", "passed", "curlExitCode", "decodeError", "error",
        ) if key in source}
        ray = source.get("cfRay") or source.get("headers", {}).get("cf-ray")
        row["cfRay"] = ray
        matches = by_ray.get(ray.split("-")[0].lower(), []) if ray else []
        row.update(matchStatus="unmatched", outcome=None, workerVersion=None, exceptions=[])
        if len(matches) > 1:
            row["matchStatus"] = "ambiguous"
        elif matches:
            event = matches[0]
            version = event.get("scriptVersion", {}).get("id")
            row.update(
                matchStatus="matched" if version == expected_version else "version-mismatch",
                workerVersion=version,
                outcome=event.get("outcome"),
                exceptions=[error["message"] for error in event.get("exceptions", [])],
            )
        rows.append(row)
    cases = []
    for route, accept in sorted({(r["route"], r["acceptEncoding"]) for r in rows}):
        case = [r for r in rows if r["route"] == route and r["acceptEncoding"] == accept]
        cases.append({
            "route": route, "acceptEncoding": accept, "requests": len(case),
            "intactBodies": sum(r["passed"] for r in case),
            "matches": dict(collections.Counter(r["matchStatus"] for r in case)),
            "outcomes": dict(collections.Counter(r["outcome"] for r in case if r["matchStatus"] == "matched")),
        })
    return {
        "compatibilityDate": data["compatibilityDate"],
        "calleeVersion": data.get("calleeVersion"),
        "expectedCallerVersion": expected_version,
        "tailParseError": parse_error, "cases": cases, "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("tail", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--caller-version", required=True, help="Expected caller version ID printed by deployment")
    args = parser.parse_args()
    events, error = parse_tail(args.tail.read_text())
    report = correlate(json.loads(args.matrix.read_text()), events, args.caller_version, error)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for case in report["cases"]:
        print(case["acceptEncoding"], case["route"], "intact", case["intactBodies"], "/", case["requests"],
              "matches", case["matches"], "outcomes", case["outcomes"])
    if error:
        print("Tail JSON incomplete or invalid:", error)
    healthy = report["rows"] and not error and all(
        r["passed"] and r["matchStatus"] == "matched" and r["outcome"] == "ok" and not r["exceptions"]
        for r in report["rows"]
    )
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
