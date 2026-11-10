"""
Trusted source-address extraction for CRM rate limiting (scope 2.5b).

Client-supplied forwarding headers are never trusted.
  * On Vercel (process environment VERCEL=1, which a request cannot influence) the only source is the header the
    platform itself sets, `x-vercel-forwarded-for`. Every other address-carrying header is ignored, and the socket
    peer is not used (the runtime may rewrite it from X-Forwarded-For).
  * Anywhere else (local development, tests, another host) only the socket peer is used; every forwarded header,
    including x-vercel-forwarded-for, is ignored.
A value that is not exactly one valid IP literal goes to a single shared "unattributed" bucket with the same limits.
IPv4 is used as a /32; IPv6 as its /64; an IPv4-mapped IPv6 address becomes IPv4.
"""

import ipaddress

import config

UNATTRIBUTED = "unattributed"
_FORBIDDEN = set(", \t\r\n;[]/\\")


def normalise(raw):
    """Return the bucket string for one IP literal, or None if the value is not exactly one valid address."""
    if raw is None or not isinstance(raw, str):
        return None
    v = raw.strip()
    if not v or len(v) > 45 or any(c in _FORBIDDEN for c in v) or "%" in v:
        return None
    try:
        ip = ipaddress.ip_address(v)
    except ValueError:
        return None
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return str(ip)


def source_bucket(request) -> str:
    if config.ON_VERCEL:
        return normalise(request.headers.get("x-vercel-forwarded-for")) or UNATTRIBUTED
    client = getattr(request, "client", None)
    return normalise(getattr(client, "host", None)) or UNATTRIBUTED
