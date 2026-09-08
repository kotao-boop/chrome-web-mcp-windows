"""Public-network-only HTTP proxy used for every browser request.

Resolve once, reject the entire answer if any address is non-public, and connect
to a numeric address from that answer. Chromium never resolves upstream targets
itself. CONNECT tunnels remain pinned to that socket for their entire lifetime.
"""

from __future__ import annotations

import ipaddress
import select
import socket
import socketserver
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler

_TRANSITION_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32",
))


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # Translation/tunneling can reach a private IPv4 endpoint even though
        # the outer IPv6 address appears global. Do not delegate that policy to
        # a NAT64/6to4/Teredo gateway outside this process.
        if any(address in network for network in _TRANSITION_NETWORKS):
            return False
        if address.ipv4_mapped is not None:
            return _public_address(str(address.ipv4_mapped))
    return True


def public_addresses(host: str, port: int) -> list[tuple]:
    normalized = host.lower().rstrip(".")
    if (not normalized or "%" in normalized
            or normalized in {"localhost", "metadata.google.internal", "metadata.goog"}
            or normalized.endswith((".localhost", ".local"))):
        raise ValueError("Blocked: local or metadata hostname")
    try:
        answers = socket.getaddrinfo(normalized, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Blocked: hostname could not be resolved safely") from exc
    if not answers or any(not _public_address(str(item[4][0])) for item in answers):
        raise ValueError("Blocked: URL resolves to a non-public address")
    return answers


def connect_public(host: str, port: int) -> socket.socket:
    if not 1 <= port <= 65535:
        raise ValueError("Blocked: invalid port")
    answers = public_addresses(host, port)
    last_error: OSError | None = None
    for family, kind, proto, _, address in answers:
        upstream = socket.socket(family, kind, proto)
        try:
            upstream.settimeout(10)
            # address is numeric: no second DNS lookup / DNS rebinding window.
            upstream.connect(address)
            return upstream
        except OSError as exc:
            last_error = exc
            upstream.close()
    raise OSError("Public destination could not be reached") from last_error


class _ProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self):
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", 0), _ProxyHandler)

    def handle_error(self, request, client_address):
        """Do not print normal browser/socket shutdown races to stderr."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Do not buffer bytes that need to be relayed after parsing the headers.
    rbufsize = 0
    timeout = 15

    def log_message(self, *args):
        # Never log browsing URLs, credentials, or HTTP traffic to MCP stdout.
        pass

    def _relay(self, upstream: socket.socket) -> None:
        peers = (self.connection, upstream)
        while not getattr(self.server, "stopping").is_set():
            ready, _, _ = select.select(peers, [], [], 0.25)
            for source in ready:
                data = source.recv(65536)
                if not data:
                    return
                target = upstream if source is self.connection else self.connection
                target.sendall(data)

    def _proxy(self, tunnel: bool) -> None:
        self.close_connection = True
        try:
            parsed = urllib.parse.urlsplit("//" + self.path if tunnel else self.path)
            if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                    or (not tunnel and parsed.scheme != "http")
                    or (tunnel and (parsed.path or parsed.query or parsed.fragment))):
                raise ValueError("Blocked: invalid proxy destination")
            port = parsed.port or (443 if tunnel else 80)
            upstream = connect_public(parsed.hostname, port)
        except (ValueError, OSError):
            self.send_error(403, "Blocked or unreachable public-network destination")
            return
        try:
            with upstream:
                if tunnel:
                    self.send_response(200, "Connection established")
                    self.end_headers()
                else:
                    path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
                    headers = [f"{self.command} {path} HTTP/1.1", f"Host: {parsed.netloc}"]
                    for key, value in self.headers.items():
                        if key.lower() not in {"host", "proxy-connection", "proxy-authorization", "connection"}:
                            headers.append(f"{key}: {value}")
                    connection = "upgrade" if self.headers.get("Upgrade", "").lower() == "websocket" else "close"
                    headers.extend([f"Connection: {connection}", "", ""])
                    upstream.sendall("\r\n".join(headers).encode("iso-8859-1"))
                self._relay(upstream)
        except OSError:
            pass

    def do_CONNECT(self):
        self._proxy(True)

    def do_GET(self):
        self._proxy(False)

    do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_GET


class PublicNetworkProxy:
    def __init__(self):
        self.server = _ProxyServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
