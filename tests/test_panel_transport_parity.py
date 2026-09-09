"""The panel has two transports, and the frontend cannot tell which it is on.

The native shell serves the panel over an in-process bridge; when it will not
start, the same page is served over loopback HTTP. The JavaScript is identical
either way, so an endpoint that only one transport routes is a control that
works or silently does nothing depending on how the app happened to start.

That is not hypothetical. /api/telemetry-receiver existed only on the bridge.
The fallback panel is told the receiver is manageable, draws the switch, posts
here, gets a 404, and the frontend's `if (!response.ok) return` swallows it --
so on any machine where the native shell was blocked, the telemetry switch was
live-looking and inert.

These tests read the endpoints out of the page itself rather than listing them,
so a new one added to the UI fails here until both transports serve it.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from sandglass import serve

PAGE = Path(__file__).resolve().parents[1] / "sandglass" / "web" / "index.html"


def posted_endpoints() -> set[str]:
    """Every /api path the page sends a POST to."""
    text = PAGE.read_text(encoding="utf-8")
    found = set()
    for match in re.finditer(r'fetch\(\s*"(/api/[a-zA-Z0-9/_-]+)"\s*,\s*\{', text):
        tail = text[match.end():match.end() + 200]
        if re.search(r'method\s*:\s*"POST"', tail):
            found.add(match.group(1))
    return found


def fetched_endpoints() -> set[str]:
    """Every literal /api path the page reads with GET."""
    text = PAGE.read_text(encoding="utf-8")
    return set(re.findall(r'fetch\(\s*"(/api/[a-zA-Z0-9/_-]+)"\s*\)', text))


class _Receiver:
    def __init__(self):
        self.on = False

    def status(self):
        return {"state": "ready" if self.on else "disabled", "manageable": True,
                "error": "", "providers": {}}

    def enable(self):
        self.on = True
        return True

    def disable(self):
        self.on = False

    def provider_enabled(self, provider):
        return False

    def set_provider_enabled(self, provider, enabled):
        return None

    def enabled_providers(self):
        return []


class PanelTransportParityTests(unittest.TestCase):
    def test_update_capability_identifies_standalone_and_desktop_transports(self):
        from sandglass import desktop

        with patch("sandglass.update.available_update",
                   return_value={"version": "9.9.9"}):
            standalone = serve.api_payload("/api/update", live_quota=False)
            native = desktop._desktop_api(_Receiver(), "GET", "/api/update")
        self.assertFalse(standalone["apply_supported"])
        self.assertTrue(native["apply_supported"])

    def test_http_fallback_advertises_apply_and_standalone_http_rejects_apply(self):
        """Exercise both HTTP modes, including the unsupported POST contract."""
        def start(update_apply=None):
            handler = partial(
                serve.Handler, since=None, live_quota=False, allow_otlp=False,
                update_apply=update_apply,
            )
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)
            return httpd

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ), patch("sandglass.update.available_update",
                 return_value={"version": "9.9.9"}):
            fallback = start(update_apply=lambda body: {"ok": True})
            payload = json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{fallback.server_port}/api/update", timeout=10
            ).read())
            self.assertTrue(payload["apply_supported"])

            standalone = start()
            request = urllib.request.Request(
                f"http://127.0.0.1:{standalone.server_port}/api/update/apply",
                method="POST", data=b"{}", headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=10)
            self.assertEqual(raised.exception.code, 404)

    def test_the_page_posts_to_something(self):
        """A guard on the guard: an empty set would make the rest vacuous."""
        self.assertTrue(posted_endpoints(), "没有从页面里解析到任何 POST 端点")

    def test_every_posted_endpoint_is_routed_over_http(self):
        receiver = _Receiver()
        from sandglass.desktop import apply_telemetry_receiver

        from sandglass.desktop import apply_update_request

        handler = partial(
            serve.Handler, since=None, live_quota=False, allow_otlp=False,
            telemetry_receiver=receiver.status,
            telemetry_apply=partial(apply_telemetry_receiver, receiver),
            update_apply=partial(apply_update_request, None),
        )
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ), patch("sandglass.desktop._save_state", lambda **kw: None):
            for path in sorted(posted_endpoints()):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}{path}", method="POST",
                    data=json.dumps({"enabled": True}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                try:
                    status = urllib.request.urlopen(request, timeout=10).status
                except urllib.error.HTTPError as exc:
                    status = exc.code
                # 400 means routed and the payload was wrong, which is fine here:
                # only 404 says the transport does not know the endpoint at all.
                self.assertNotEqual(status, 404, f"HTTP 通道没有路由 {path}")

    def test_every_posted_endpoint_is_routed_over_the_native_bridge(self):
        from sandglass import desktop

        receiver = _Receiver()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ), patch.object(desktop, "_save_state", lambda **kw: None):
            for path in sorted(posted_endpoints()):
                try:
                    desktop._desktop_api(
                        receiver, "POST", path, json.dumps({"enabled": True})
                    )
                except KeyError:  # the bridge's "no such route"
                    self.fail(f"原生桥没有路由 {path}")
                except (ValueError, TypeError, AttributeError):
                    pass  # routed; the fixture payload just did not suit it

    def test_every_fetched_endpoint_is_routed_by_the_shared_get_api(self):
        self.assertTrue(fetched_endpoints(), "没有从页面里解析到任何 GET 端点")
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ):
            for path in sorted(fetched_endpoints()):
                try:
                    serve.api_payload(path, live_quota=False)
                except KeyError:
                    self.fail(f"共享 GET API 没有路由 {path}")


class OtlpListenerSurfaceTests(unittest.TestCase):
    """The telemetry listener must expose what it says it exposes.

    It was split into its own listener so that turning telemetry on would not
    put account data on a port -- "never serves dashboard or account data". But
    only do_GET and do_HEAD were overridden, and the /api routes are matched
    before the OTLP gate, so POST reached three of them: user-source configure,
    user-source reset, and the attribution mode switch. Any local process could
    reconfigure attribution through the telemetry port.
    """

    def _post(self, handler_cls, path):
        """POST once against a throwaway listener, and say why if it cannot.

        Only HTTPError was caught, so a refused connection or a timeout came out
        as a bare URLError and the run recorded an error with no cause. That
        happened three times across three different runners during 2026-09-05/06
        -- roughly one full suite in five, never once when this class ran alone
        (60 consecutive passes), and not in 10 clean full runs afterwards. Until
        it is understood, the least it can do is name itself.

        The listener is created inside an isolated state home: the panel
        handler's account routes can write product state, and a later change
        that posted real protobuf at /v1/logs would ingest into whatever
        SANDGLASS_HOME the suite inherited.
        """
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(handler_cls, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            started = time.monotonic()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}{path}", method="POST",
                    data=b"{}", headers={"Content-Type": "application/json"},
                )
                try:
                    return urllib.request.urlopen(request, timeout=10).status
                except urllib.error.HTTPError as exc:
                    return exc.code
                except urllib.error.URLError as exc:
                    self.fail(
                        f"POST {path} to 127.0.0.1:{httpd.server_port} never got a "
                        f"reply after {time.monotonic() - started:.1f}s: "
                        f"{type(exc.reason).__name__}: {exc.reason}. "
                        f"serve_forever thread alive={thread.is_alive()}, "
                        f"live threads={threading.active_count()}"
                    )
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                if thread.is_alive():
                    self.fail(f"the listener for {path} outlived its shutdown")

    ACCOUNT_APIS = ("/api/user-sources/configure", "/api/user-sources/reset",
                    "/api/product-mode")

    def test_the_telemetry_listener_routes_no_account_api(self):
        for path in self.ACCOUNT_APIS:
            self.assertEqual(self._post(serve.OtlpHandler, path), 404,
                             f"遥测端口不应路由 {path}")

    def test_it_still_ingests(self):
        """The counterpart: 404 on everything would also pass the test above."""
        self.assertNotEqual(self._post(serve.OtlpHandler, "/v1/logs"), 404)

    def test_a_refusal_answers_instead_of_aborting_the_connection(self):
        """This is what made the tests above fail every so often.

        Every refusal in this handler happens before the request body is read:
        wrong content type, no length, path not served. Closing a socket with
        unread bytes still in its receive buffer is an abortive close on
        Windows, so the client's next read fails with WSAECONNABORTED (10053)
        instead of seeing the status that was just written.

        Measured against this handler before the fix: a 4 MiB body to a path it
        answers 404 came back aborted 9 times in 10, 1 MiB 3 times in 10, and
        the 2-byte body `_post` sends almost always got through. That last part
        is the whole reason this looked like a rare, unrelated flake -- and why
        this test sends a large body: at 4 MiB the defect is not rare.
        """
        cases = ((b"/api/user-sources/configure", "application/json", 404),
                 (b"/v1/logs", "text/plain", 415))
        body = b"x" * (4 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(serve.OtlpHandler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                for raw_path, content_type, expected in cases:
                    path = raw_path.decode()
                    for attempt in range(5):
                        with self.subTest(path=path, attempt=attempt):
                            conn = http.client.HTTPConnection(
                                "127.0.0.1", httpd.server_port, timeout=10
                            )
                            try:
                                conn.request("POST", path, body=body,
                                             headers={"Content-Type": content_type})
                                status = conn.getresponse().status
                            except OSError as exc:
                                self.fail(
                                    f"POST {path} was aborted instead of answered "
                                    f"{expected}: {type(exc).__name__}: {exc}"
                                )
                            finally:
                                conn.close()
                            self.assertEqual(status, expected)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

    def test_a_dead_listener_is_a_failure_with_a_cause(self):
        """The flake was an ERROR, which named no listener and no reason."""
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(
                ConnectionRefusedError(10061, "connection refused")
            ),
        ):
            with self.assertRaises(self.failureException) as raised:
                self._post(serve.OtlpHandler, "/v1/logs")
        message = str(raised.exception)
        self.assertIn("never got a reply", message)
        self.assertIn("ConnectionRefusedError", message)

    def test_the_panel_listener_does_route_them(self):
        """Where they belong, they must still work."""
        for path in self.ACCOUNT_APIS:
            self.assertNotEqual(self._post(serve.Handler, path), 404,
                                f"面板通道应当路由 {path}")


if __name__ == "__main__":
    unittest.main()
