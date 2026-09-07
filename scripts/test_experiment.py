"""Exercise real wire failures and evidence-correlation failure modes."""

import gzip
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from correlate import correlate, parse_tail
from matrix import request

BODY = b'{"ok":true}'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        raw = gzip.compress(BODY)
        if self.path == "/":
            raw = raw[:10]
        elif self.path == "/invalid":
            raw = b"invalid gzip"
        self.send_response(200)
        self.send_header("Content-Encoding", "gzip")
        self.send_header("CF-Ray", "abc-SJC")
        self.send_header("Content-Length", len(raw) + (20 if self.path == "/aborted" else 0))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def event(ray="abc", version="expected", outcome="ok", exceptions=None):
    return {
        "event": {"request": {"headers": {"cf-ray": ray, "cf-connecting-ip": "private"}}},
        "scriptVersion": {"id": version}, "outcome": outcome, "exceptions": exceptions or [],
    }


class ExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_complete_and_truncated_gzip(self):
        good = request(self.url, "/no-await", "gzip")
        self.assertTrue(good["passed"])
        bad = request(self.url, "/", "gzip")
        self.assertFalse(bad["passed"])
        self.assertEqual(bad["wireBytes"], 10)
        self.assertEqual(bad["cfRay"], "abc-SJC")
        self.assertIn("decodeError", bad)

    def test_transport_failure_retains_body(self):
        row = request(self.url, "/aborted", "gzip")
        self.assertFalse(row["passed"])
        self.assertNotEqual(row["curlExitCode"], 0)
        self.assertEqual(row["body"], BODY.decode())
        self.assertGreater(row["wireBytes"], 10)

    def test_invalid_compression_is_a_recorded_failure(self):
        row = request(self.url, "/invalid", "gzip")
        self.assertFalse(row["passed"])
        self.assertEqual(row["wireHex"], b"invalid gzip".hex())

    def test_banners_and_partial_tail_are_distinguished(self):
        events, error = parse_tail("pnpm startup\n" + json.dumps(event(), indent=2) + '\n{"incomplete":')
        self.assertEqual(len(events), 1)
        self.assertIsNotNone(error)
        events, error = parse_tail("pnpm startup\n" + json.dumps(event(), indent=2))
        self.assertEqual(len(events), 1)
        self.assertIsNone(error)

    def test_body_success_does_not_hide_stream_exception(self):
        data = {"compatibilityDate": "2026-04-09", "rows": [{
            "route": "/no-gzip", "acceptEncoding": "gzip", "passed": True,
            "headers": {"cf-ray": "abc-SJC", "private": "secret"},
        }]}
        events = [event(outcome="exception", exceptions=[{"message": "stream ended early"}])]
        report = correlate(data, events, "expected")
        self.assertEqual(report["cases"][0]["intactBodies"], 1)
        self.assertEqual(report["cases"][0]["outcomes"], {"exception": 1})
        self.assertNotIn("secret", json.dumps(report))
        self.assertNotIn("private", json.dumps(report))

    def test_missing_duplicate_and_wrong_version_are_not_success(self):
        data = {"compatibilityDate": "2026-04-09", "rows": [
            {"route": "/", "acceptEncoding": "gzip", "passed": True, "cfRay": ray}
            for ray in ["missing-SJC", "duplicate-SJC", "old-SJC"]
        ]}
        events = [event("duplicate"), event("duplicate"), event("old", "earlier-version")]
        report = correlate(data, events, "expected")
        self.assertEqual([r["matchStatus"] for r in report["rows"]],
                         ["unmatched", "ambiguous", "version-mismatch"])
        self.assertEqual(report["cases"][0]["outcomes"], {})

    def test_matrix_cli_writes_all_cases_and_exits_nonzero_for_broken_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "matrix.json"
            result = subprocess.run([
                sys.executable, str(Path(__file__).with_name("matrix.py")), self.url, str(output),
                "--repetitions", "1", "--compatibility-date", "2026-04-09", "--callee-version", "fixture",
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            rows = json.loads(output.read_text())["rows"]
            self.assertEqual(len(rows), 12)
            self.assertEqual(sum(not r["passed"] for r in rows), 2)
            self.assertEqual({r["acceptEncoding"] for r in rows}, {"gzip", "identity"})

    def test_matcher_cli_exit_status_includes_worker_exceptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = {"compatibilityDate": "2026-04-09", "rows": [{
                "route": "/", "acceptEncoding": "gzip", "passed": True, "cfRay": "abc-SJC",
            }]}
            (root / "matrix.json").write_text(json.dumps(data))
            for outcome, exit_code in [("ok", 0), ("exception", 1)]:
                (root / "tail.json").write_text(json.dumps(event(outcome=outcome), indent=2))
                result = subprocess.run([
                    sys.executable, str(Path(__file__).with_name("correlate.py")),
                    str(root / "matrix.json"), str(root / "tail.json"), str(root / "report.json"),
                    "--caller-version", "expected",
                ], capture_output=True, text=True)
                self.assertEqual(result.returncode, exit_code, result.stderr)
                self.assertEqual(json.loads((root / "report.json").read_text())["rows"][0]["outcome"], outcome)


if __name__ == "__main__":
    unittest.main()
