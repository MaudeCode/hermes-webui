"""HTTP-server helpers shared by the WebUI entry point."""

from socketserver import TCPServer


def bind_without_reverse_dns(server) -> None:
    """Bind an HTTP server without Python's unnecessary reverse-DNS lookup."""
    TCPServer.server_bind(server)
    host, port = server.server_address[:2]
    server.server_name = str(host)
    server.server_port = int(port)
