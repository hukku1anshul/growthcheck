"""A one-host reverse proxy, so a browser can reach a site the local DNS cannot.

    python tools/govproxy.py mplads.mospi.gov.in 8899
    # then open http://localhost:8899/digigov/dashboard.html

WHY
---
This machine's resolver returns nothing for any `.gov.in` hostname while every
other domain resolves normally, and changing the system resolver needs
administrator rights the user may not have. `ingest/dns_fallback.py` solves that
for Python by falling back to DNS-over-HTTPS, but a browser uses the operating
system's resolver and cannot be patched the same way.

That matters because some government portals ship obfuscated JavaScript that
builds its own API calls at runtime. Guessing those endpoints from the outside is
slow, fragile and produces brittle extractors. Loading the real page and watching
what it actually requests is both faster and far more accurate - and this proxy is
what makes that possible without touching system settings.

Because the proxy fronts exactly ONE upstream host at the server root, every
relative URL in the page ("/rest/...", "/libs/...") resolves through the proxy
unchanged, so the page behaves as it would normally.

Scope: development aid only. It is not part of the ingest path, it binds to
localhost, and it forwards only to the single host named on the command line.
"""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from ingest.dns_fallback import install, resolve_doh  # noqa: E402

install()

UPSTREAM_HOST = ""
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "politicalfindings/0.1 (open-source public-data research)",
})

# Hop-by-hop headers must not be forwarded.
SKIP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
    "content-length", "strict-transport-security",
}


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter output
        pass

    def _forward(self, method: str) -> None:
        url = f"https://{UPSTREAM_HOST}{self.path}"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in SKIP | {"host", "accept-encoding"}
        }
        headers["Host"] = UPSTREAM_HOST
        headers["Referer"] = f"https://{UPSTREAM_HOST}/digigov/dashboard.html"
        headers["Origin"] = f"https://{UPSTREAM_HOST}"

        try:
            r = SESSION.request(
                method, url, headers=headers, data=body,
                timeout=90, allow_redirects=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"proxy error: {exc}".encode())
            print(f"  {method} {self.path} -> ERROR {type(exc).__name__}")
            return

        content = r.content
        self.send_response(r.status_code)
        for k, v in r.headers.items():
            if k.lower() in SKIP:
                continue
            if k.lower() == "location":
                # keep redirects inside the proxy
                v = v.replace(f"https://{UPSTREAM_HOST}", "").replace(
                    f"http://{UPSTREAM_HOST}", "")
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

        marker = " <== API" if "/rest/" in self.path else ""
        print(f"  {method} {self.path[:88]} -> {r.status_code} "
              f"({len(content):,}B){marker}", flush=True)
        if "/rest/" in self.path and body:
            print(f"        request body: {body[:220]!r}", flush=True)
        if "/rest/" in self.path and content and len(content) < 600:
            print(f"        response:     {content[:300]!r}", flush=True)

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

    def do_OPTIONS(self):
        self._forward("OPTIONS")


def main() -> int:
    global UPSTREAM_HOST
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    UPSTREAM_HOST = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8899

    ip = resolve_doh(UPSTREAM_HOST)
    print(f"upstream {UPSTREAM_HOST} -> {ip or 'system resolver'}")
    print(f"serving  http://localhost:{port}/  (Ctrl-C to stop)\n")
    ThreadingHTTPServer(("127.0.0.1", port), Proxy).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
