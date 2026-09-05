import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from chrome_web_mcp import network


@pytest.mark.parametrize("host", ["127.0.0.1", "127.1", "2130706433", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "localhost", "metadata.google.internal", "64:ff9b::7f00:1", "2002:7f00:1::", "224.0.0.1"])
def test_rejects_private_addresses(host):
    with pytest.raises(ValueError, match="Blocked"):
        network.public_addresses(host, 80)


def test_rejects_mixed_dns_answers_before_opening_socket(monkeypatch):
    monkeypatch.setattr(network.socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    monkeypatch.setattr(network.socket, "socket", lambda *a: pytest.fail("must reject before connecting"))
    with pytest.raises(ValueError, match="non-public"):
        network.connect_public("mixed.invalid", 443)


def test_connect_pins_first_validated_dns_answer(monkeypatch):
    queries = []
    connected = []
    def resolve(host, port, **kwargs):
        queries.append(host)
        ip = "8.8.8.8" if len(queries) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    class FakeSocket:
        def settimeout(self, value): pass
        def connect(self, address): connected.append(address)
    monkeypatch.setattr(network.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(network.socket, "socket", lambda *a: FakeSocket())
    network.connect_public("rebind.invalid", 443)
    assert queries == ["rebind.invalid"]
    assert connected == [("8.8.8.8", 443)]


def exchange(proxy, request):
    with socket.create_connection(proxy.server.server_address, timeout=5) as conn:
        conn.sendall(request)
        output = b""
        while chunk := conn.recv(65536):
            output += chunk
        return output


@pytest.mark.parametrize("raw_request", [
    b"GET http://127.0.0.1:9/ HTTP/1.1\r\nHost: localhost\r\n\r\n",
    b"CONNECT 127.0.0.1:9 HTTP/1.1\r\nHost: localhost\r\n\r\n",
    b"GET http://user:password@example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n",
    b"CONNECT [::1]:9 HTTP/1.1\r\nHost: localhost\r\n\r\n",
])
def test_proxy_rejects_private_or_credential_destinations(raw_request):
    proxy = network.PublicNetworkProxy()
    try:
        assert b"403" in exchange(proxy, raw_request).split(b"\r\n")[0]
    finally:
        proxy.close()


def test_http_proxy_preserves_request_body_and_strips_proxy_credentials(monkeypatch):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"fixture response")
        def log_message(self, *args): pass
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    def connect_fixture(host, port):
        assert (host, port) == ("fixture.invalid", 80)
        return socket.create_connection(fixture.server_address, timeout=5)
    monkeypatch.setattr(network, "connect_public", connect_fixture)
    proxy = network.PublicNetworkProxy()
    try:
        response = exchange(proxy, b"POST http://fixture.invalid/path?q=test HTTP/1.1\r\nHost: fixture.invalid\r\nProxy-Authorization: secret\r\nContent-Length: 5\r\n\r\nhello")
        assert b"fixture response" in response
        assert requests[0][0] == "/path?q=test"
        assert requests[0][2] == b"hello"
        assert "Proxy-Authorization" not in requests[0][1]
    finally:
        proxy.close()
        fixture.shutdown()
        fixture.server_close()
