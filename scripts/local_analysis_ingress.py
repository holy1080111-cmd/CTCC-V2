"""Local TCP ingress for one AF_UNIX backend in the analysis installation."""

from __future__ import annotations

import asyncio
import http.client
import socket
import sys

_SOCKET_PATH = "/run/ctcc-analysis/api.sock"
_MAX_CONNECTIONS = 32
_RELAY_TIMEOUT_SECONDS = 45


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(65_536):
        writer.write(data)
        await writer.drain()
    writer.write_eof()


async def _relay(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    backend_writer = None
    try:
        async with asyncio.timeout(_RELAY_TIMEOUT_SECONDS):
            backend_reader, backend_writer = await asyncio.open_unix_connection(
                _SOCKET_PATH
            )
            try:
                async with asyncio.TaskGroup() as flows:
                    flows.create_task(_copy(reader, backend_writer))
                    flows.create_task(_copy(backend_reader, writer))
            except* OSError:
                pass
    except (TimeoutError, OSError):
        pass
    finally:
        if backend_writer is not None:
            backend_writer.close()
        writer.close()


async def _serve() -> None:
    connections = 0

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        nonlocal connections
        if connections >= _MAX_CONNECTIONS:
            writer.close()
            return
        connections += 1
        try:
            await _relay(reader, writer)
        finally:
            connections -= 1

    server = await asyncio.start_server(
        handle, host="0.0.0.0", port=8000, family=socket.AF_INET, backlog=32
    )
    async with server:
        await server.serve_forever()


def _check_backend() -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as session:
            session.settimeout(3)
            session.connect(_SOCKET_PATH)
            session.sendall(
                b"GET /readiness HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            )
            response = http.client.HTTPResponse(session)
            try:
                response.begin()
                return response.status == 200
            finally:
                response.close()
    except (OSError, http.client.HTTPException):
        return False


def main() -> int:
    args = sys.argv[1:]
    if args == ["--check-backend"]:
        return 0 if _check_backend() else 1
    if args:
        return 2
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
