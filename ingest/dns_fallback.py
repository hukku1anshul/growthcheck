"""DNS-over-HTTPS fallback for hosts the local resolver cannot see.

WHY THIS EXISTS
---------------
On the machine this project was built on, no `.gov.in` hostname resolved at all -
mplads.mospi.gov.in, data.gov.in, sansad.in and mospi.gov.in all failed with
getaddrinfo errors, while every other host resolved fine. The domains were
healthy: Google's public resolver returned A records instantly, and connecting
to the returned IP with correct SNI gave HTTP 200 and a valid certificate. The
local router simply would not resolve that TLD.

That is an unpleasantly common situation for exactly the people this project is
for. A citizen trying to read their own government's data is often behind an ISP
resolver that mangles, filters or fails on government domains, and "reconfigure
your router as administrator" is not an acceptable prerequisite for reading a
public budget.

So: when normal resolution fails, ask a public DNS-over-HTTPS resolver, cache the
answer, and let the connection proceed.

WHAT THIS DOES NOT DO
---------------------
It does not weaken TLS. Resolution and authentication are separate concerns: we
override only the hostname -> IP step, and the TLS handshake still uses the real
hostname for SNI and still verifies the certificate chain against it. A hostile
DNS answer therefore cannot impersonate the site - it can only fail to connect.
Certificate verification is never disabled anywhere in this project.

It is also a fallback, not a default. The system resolver is always tried first,
so a machine with working DNS behaves exactly as before and makes no requests to
a third-party resolver.
"""

from __future__ import annotations

import socket
import threading

import requests

# Public DoH endpoints, tried in order. Both return the same JSON shape.
DOH_ENDPOINTS = (
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
)

_overrides: dict[str, str] = {}
_lock = threading.Lock()
_installed = False
_real_getaddrinfo = socket.getaddrinfo


def resolve_doh(host: str, timeout: float = 15.0) -> str | None:
    """Look up an A record over HTTPS. Returns an IPv4 string, or None."""
    for endpoint in DOH_ENDPOINTS:
        try:
            r = requests.get(
                endpoint,
                params={"name": host, "type": "A"},
                headers={"Accept": "application/dns-json"},
                timeout=timeout,
            )
            data = r.json()
            for answer in data.get("Answer", []):
                if answer.get("type") == 1 and answer.get("data"):
                    return answer["data"]
        except Exception:  # noqa: BLE001 - try the next resolver
            continue
    return None


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _real_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        ip = _overrides.get(host)
        if ip is None:
            ip = resolve_doh(host)
            if ip:
                with _lock:
                    _overrides[host] = ip
        if not ip:
            raise
        # Return the address the socket layer expects, with the IP substituted.
        # requests/urllib3 still carry the original hostname through to TLS, so
        # SNI and certificate verification are unaffected.
        return _real_getaddrinfo(ip, port, family, type, proto, flags)


def install() -> None:
    """Enable the fallback process-wide. Safe to call more than once."""
    global _installed
    if _installed:
        return
    socket.getaddrinfo = _patched_getaddrinfo
    _installed = True


def status() -> dict[str, str]:
    """Hosts currently being resolved via DoH, for reporting."""
    return dict(_overrides)
