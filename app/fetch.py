"""Download a picture from a link, for the "paste a link" import.

The server fetches whatever address it is given, so this only speaks http and
https, only to the public internet, and gives up on anything large or slow.
"""
import http.client
import ipaddress
import ssl
import time
import urllib.error
import urllib.request
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

SOCKET_TIMEOUT = 10
TOTAL_TIMEOUT = 30
CHUNK = 64 * 1024
# Some hosts (Wikimedia, for one) refuse the default Python user agent.
USER_AGENT = "FrameTVArt (+https://github.com/nathaniel-sheetz/frame-it)"
TOO_SLOW = "That download took too long."

# NAT64 carries an IPv4 address in its last 32 bits, which is where the connection ends up.
NAT64 = ipaddress.ip_network("64:ff9b::/96")
# Old IPv6 forms that is_global lets through but that can lead somewhere private.
NOT_PUBLIC = (
    ipaddress.ip_network("::/96"),  # IPv4-compatible
    ipaddress.ip_network("::ffff:0:0:0/96"),  # IPv4-translated
    ipaddress.ip_network("fec0::/10"),  # site-local
)


class FetchError(Exception):
    """A download that failed, with the HTTP status to answer the browser with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip in NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif any(ip in network for network in NOT_PUBLIC):
            return False
    return ip.is_global


def _is_neighbour(peer: str, own: str) -> bool:
    """Whether both ends of a connection are in the same IPv6 /64, which is one home network.

    With IPv6 from the provider, the devices at home have addresses that count
    as public, so is_global alone would let a link reach them.
    """
    try:
        peer, own = ipaddress.ip_address(peer), ipaddress.ip_address(own)
    except ValueError:
        return True
    if peer.version != 6 or own.version != 6 or peer.ipv4_mapped or own.ipv4_mapped:
        return False
    return int(peer) >> 64 == int(own) >> 64


class _GuardedConnection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        # Checked on the socket, not on a DNS lookup made beforehand, so a name
        # that resolves differently the second time cannot slip past, and
        # every redirect is checked the same way.
        peer = self.sock.getpeername()[0]
        if not _is_public(peer) or _is_neighbour(peer, self.sock.getsockname()[0]):
            raise FetchError("Only links to the public internet can be fetched.")


class _GuardedHTTPSConnection(http.client.HTTPSConnection, _GuardedConnection):
    """HTTPSConnection.connect() reaches the guard above before it starts TLS."""


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_GuardedConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self):
        self._tls = ssl.create_default_context()
        super().__init__(context=self._tls)

    def https_open(self, req):
        return self.do_open(_GuardedHTTPSConnection, req, context=self._tls)


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, deadline: float):
        self._deadline = deadline

    def http_error_302(self, req, fp, code, msg, headers):
        # The stock handler reads the whole body of a redirect before following
        # it, which would get around both the size limit and the deadline.
        fp.close()
        if time.monotonic() > self._deadline:
            raise FetchError(TOO_SLOW, 504)
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _opener(deadline: float) -> urllib.request.OpenerDirector:
    # Built by hand: build_opener() would add proxy, ftp:, file: and data: support.
    opener = urllib.request.OpenerDirector()
    for handler in (
        _HTTPHandler(),
        _HTTPSHandler(),
        _RedirectHandler(deadline),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.UnknownHandler(),
    ):
        opener.add_handler(handler)
    return opener


def _filename(url: str) -> str:
    return PurePosixPath(unquote(urlsplit(url).path)).name[-100:] or "image.jpg"


def fetch(url: str, max_bytes: int) -> tuple[bytes, str]:
    """Return the body of an http(s) link and a file name taken from its address.

    Raises FetchError. The body is not checked to be a picture; the caller does that.
    """
    url = url.strip()
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        parts, host = None, None
    if not host or parts.scheme not in ("http", "https"):
        raise FetchError("Only http and https links are supported.")

    too_large = FetchError(f"File too large. Maximum size is {max_bytes // (1024 * 1024)}MB.", 413)
    deadline = time.monotonic() + TOTAL_TIMEOUT
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.5"})
        with _opener(deadline).open(request, timeout=SOCKET_TIMEOUT) as response:
            if response.headers.get_content_type() == "text/html":
                raise FetchError(
                    "That link is a web page, not a picture. Open the picture on its own and copy that address."
                )
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > max_bytes:
                raise too_large
            chunks, size = [], 0
            # read1() returns after one network read, so a slow trickle still meets the deadline.
            while chunk := response.read1(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise too_large
                if time.monotonic() > deadline:
                    raise FetchError(TOO_SLOW, 504)
                chunks.append(chunk)
            if response.length:
                # The connection closed before the announced length had arrived.
                raise FetchError("That download was cut short. Please try again.", 502)
            return b"".join(chunks), _filename(response.geturl())
    except urllib.error.HTTPError as e:
        e.close()
        raise FetchError(f"The site answered with an error ({e.code}).", 502)
    except (OSError, http.client.HTTPException, ValueError):
        # URLError is an OSError: unknown hosts, refused connections, timeouts, bad certificates.
        raise FetchError("Could not download that link.", 502)
