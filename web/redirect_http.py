#!/usr/bin/env python3
"""Tiny HTTP → HTTPS redirector for the panel (used when TLS is enabled).

Listens on the plain port so that a user typing http://server:8088 lands on
https://server:8443 instead of a TLS error.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--target-port", type=int, default=8443)
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        server_version = "Virtuality"
        sys_version = ""

        def do_GET(self):  # noqa: N802
            host = (self.headers.get("Host") or "").split(":")[0] or "localhost"
            self.send_response(301)
            self.send_header("Location", f"https://{host}:{args.target_port}{self.path}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = do_GET  # noqa: N815
        do_POST = do_GET  # noqa: N815

        def log_message(self, *_args):
            return

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
