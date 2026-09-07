import unittest
from unittest.mock import patch

from sandglass import cli
from sandglass.serve import api_payload, loopback_host


class LocalServiceBoundaryTests(unittest.TestCase):
    @patch("sandglass.serve._cached_report", return_value={"kind": "report"})
    def test_in_process_api_uses_the_same_report_query_semantics(self, report):
        self.assertEqual(
            api_payload("/api/report?since=30d", since="7d", live_quota=False),
            {"kind": "report"},
        )
        report.assert_called_once_with("30d", live_quota=False)

    def test_dashboard_accepts_only_ipv4_loopback_bindings(self):
        for host in ("localhost", "127.0.0.1", "127.23.45.67"):
            with self.subTest(host=host):
                self.assertEqual(loopback_host(host), host)
        for host in ("0.0.0.0", "192.168.1.10", "::", "::1", "sandglass.local"):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    loopback_host(host)

    def test_cli_rejects_a_non_loopback_dashboard_before_starting_it(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit) as stopped:
            cli.main(["serve", "--host", "0.0.0.0", "--no-browser"])
        self.assertEqual(stopped.exception.code, 2)


class HostileQueryTests(unittest.TestCase):
    """A query string is user input even when the user is the panel."""

    def test_a_window_longer_than_the_calendar_asks_for_everything(self):
        """It used to raise OverflowError out of the HTTP handler.

        isdigit() bounds the characters, not the number. "99999999d" builds a
        timedelta that cannot be subtracted from a real date, nothing caught it,
        and the connection was dropped with no response at all -- the caller saw
        RemoteDisconnected and a traceback went to stderr.
        """
        from sandglass.report import parse_since

        for raw in ("99999999d", "99999999h", "1" + "0" * 30 + "d"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_since(raw))

    def test_ordinary_windows_still_parse(self):
        """The counterpart: returning None for everything would also not raise."""
        from datetime import datetime, timezone

        from sandglass.report import parse_since

        now = datetime.now(timezone.utc)
        for raw, days in (("30d", 30), ("1d", 1), ("0d", 0)):
            with self.subTest(raw=raw):
                parsed = parse_since(raw)
                self.assertIsNotNone(parsed)
                self.assertAlmostEqual((now - parsed).total_seconds(), days * 86400, delta=5)

    def test_every_api_query_answers_rather_than_dropping_the_connection(self):
        import json
        import threading
        import urllib.error
        import urllib.request
        from functools import partial
        from http.server import ThreadingHTTPServer

        from sandglass import serve

        httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(serve.Handler, since=None, live_quota=False)
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        with patch.object(serve, "_cached_report", lambda since, live_quota=True: {"ok": True}):
            for path in ("/api/report?since=99999999d", "/api/report?since=99999999h",
                         "/api/report?since=abc", "/api/report?since=-5d",
                         "/api/report?since=" + "9" * 400):
                with self.subTest(path=path):
                    try:
                        status = urllib.request.urlopen(
                            f"http://127.0.0.1:{httpd.server_port}{path}", timeout=30
                        ).status
                    except urllib.error.HTTPError as exc:
                        status = exc.code
                    self.assertLess(status, 500, f"{path} 应当有响应而不是断开")


if __name__ == "__main__":
    unittest.main()
