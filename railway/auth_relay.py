"""Local HTTP proxy relay that adds Proxy-Authorization for an upstream proxy.

Chromium cannot answer the upstream's bare `Proxy-Authenticate: Basic`
challenge (no realm, connection closed), so the browser talks to this relay
unauthenticated on loopback and the relay presents the credentials.
"""

import asyncio
import base64
import os

UP_HOST = os.environ.get("UPSTREAM_PROXY_HOST", "residential-proxy.scrapeops.io")
UP_PORT = int(os.environ.get("UPSTREAM_PROXY_PORT", "8181"))
AUTH = base64.b64encode(
    f"{os.environ['UPSTREAM_PROXY_USERNAME']}:{os.environ['UPSTREAM_PROXY_PASSWORD']}".encode()
).decode()
LISTEN = int(os.environ.get("RELAY_PORT", "18181"))


async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        w.close()


async def handle(cr, cw):
    try:
        head = await cr.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        cw.close()
        return
    lines = head.split(b"\r\n")
    kept = [
        line
        for line in lines[1:]
        if line and not line.lower().startswith(b"proxy-authorization")
    ]
    out = (
        b"\r\n".join([lines[0], *kept, f"Proxy-Authorization: Basic {AUTH}".encode()])
        + b"\r\n\r\n"
    )
    try:
        ur, uw = await asyncio.open_connection(UP_HOST, UP_PORT)
    except OSError:
        cw.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        cw.close()
        return
    uw.write(out)
    await uw.drain()
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))


async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", LISTEN)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
