# Raw Header Tester

A minimal HTTP service with selectable Python and OpenResty runtimes. For normal HTTP/1.x requests with response bodies,
such as `GET`, both return the raw header lines that reach the container, excluding the request line and terminating
empty line. The response is plain text, not JSON. Header order, duplicate names, casing, whitespace, and CRLF endings
are retained.

## Local use

Run the Python service with Python 3.11 or newer:

```sh
python3 app.py --port 8080
```

When using uv locally, the equivalent command is:

```sh
uv run python app.py --port 8080
```

The optional `--port` argument takes precedence over `PORT`, and the service defaults to port 8080 when neither is
set. `PORT` remains supported so Railway can provide the listening port automatically.

Build the image and select either runtime locally:

```sh
docker build -t raw-header-tester .
docker run --rm -p 8080:8080 raw-header-tester
docker run --rm -e HEADER_SERVER=openresty -p 8080:8080 raw-header-tester
```

`HEADER_SERVER` accepts `python` or `openresty` and defaults to `python` for backward compatibility. Both image modes
read `PORT`, default to 8080, and reject non-decimal or out-of-range values before starting. The Python entry point
still reads `PORT` itself; the selector does not pass a `--port` option. Leading zeros are accepted, but input longer
than 20 digits is rejected before normalization to keep validation work bounded.

Send duplicate headers with curl:

```sh
curl --http1.1 --raw -H 'X-Test: first' -H 'X-Test: second' http://localhost:8080/
```

For exact byte-level experiments, use a raw TCP client because curl may choose its own header formatting:

```sh
printf 'GET / HTTP/1.1\r\nHost: localhost\r\nx-Mixed:\t value  \r\nX-Repeat: 1\r\nX-Repeat: 2\r\n\r\n' \
  | nc 127.0.0.1 8080
```

## Railway deployment

Create a Railway service from this repository. Railway detects the root `Dockerfile` and uses it instead of Railpack.
The image defaults to the existing Python runtime. Set `HEADER_SERVER=openresty` in the service variables to use
OpenResty; Railway supplies `PORT`. Generate a public domain in the service networking settings. No start-command
override is needed.

`railpack.json` remains in the repository for non-Docker compatibility, but Railway ignores it while the root
`Dockerfile` exists. The Docker build uses the pinned official OpenResty Bookworm image and adds only the Python 3
runtime needed by `app.py`; it does not install uv or application dependencies.

Compare Railway directly with a hostname proxied by Cloudflare:

```sh
curl --http1.1 --raw -H 'X-Route: first' -H 'X-Route: second' https://your-service.up.railway.app/
curl --http1.1 --raw -H 'X-Route: first' -H 'X-Route: second' https://headers.example.com/
```

Cloudflare, Railway's edge, and other proxies may normalize, combine, remove, add, or rebuild headers before the
request reaches this process. The service can report only the bytes delivered to its TCP socket, not the original
client-to-edge bytes. Railway's public URL may also traverse infrastructure that changes the request.

## Runtime parity and limits

The Python server accepts one HTTP/1.0 or HTTP/1.1 request per connection. It allows at most 16 KiB through the
terminating `CRLFCRLF` and waits up to five seconds for that boundary. Incomplete or malformed requests receive `400`,
timeouts receive `408`, and oversized request heads receive `431`. Error bodies are fixed and never reflect partial
input.

Byte-body parity is intended for normal HTTP/1.x requests such as `GET`. OpenResty reads the full raw request head once,
then slices away the request line and final empty-line CRLF without parsing or rebuilding fields. Nginx suppresses
response bodies for `HEAD`, so `HEAD` cannot have byte-body parity with the Python server.

Lua applies an aggregate 16 KiB request-head check after Nginx parses the request. This cannot exactly match Python's
pre-parse 16 KiB cap. Nginx uses a conventional 1 KiB initial header buffer and at most one 16 KiB large header buffer.
It can reject malformed request lines or individual oversized fields before Lua, potentially with different status
codes. Body size is unlimited because the handler does not consume or reflect the request body. The header timeout is
five seconds.

Both runtimes close the connection after one request. OpenResty disables keepalive and limits each connection to one
request to match the Python server. It intentionally does not read or discard the potentially unbounded request body.
The image uses `SIGTERM` for both runtimes. OpenResty treats it as fast termination rather than graceful shutdown; this
avoids adding a signal-translating wrapper to a header inspection service.

`ngx.req.raw_header` does not work for HTTP/2 requests. Railway's hop from its proxy to this container is expected to
use HTTP/1.x, even when the public client-facing connection negotiates HTTP/2. Any proxy may normalize, combine,
remove, add, or rebuild headers before this service receives them.

Run the standard-library tests with:

```sh
python3 -m unittest discover -v
```

## Reasoning

High-level HTTP servers parse headers into mappings or normalized field objects, which can lose ordering, duplicate
fields, original casing, whitespace, and wire line endings. Reading the request head directly with
`asyncio.start_server` preserves the byte slice that reaches the process while still enforcing strict size and time
limits. This makes direct and proxied requests easier to compare without pretending to observe bytes changed upstream.

Docker is now used because Railpack does not natively provision the official OpenResty runtime. One pinned Bookworm
image can contain OpenResty and the dependency-free Python implementation, while a small validated entry point selects
the runtime without duplicating deployment configuration. A small initial Nginx buffer avoids reserving 16 KiB for
ordinary requests, while one bounded large buffer handles request heads near the service limit. Keeping Python as the
default preserves existing behavior, and retaining `railpack.json` avoids removing the previous deployment metadata
even though Railway ignores it when the Dockerfile is present.

The container currently retains the official image's root execution model. Non-root execution was not enabled without
verified compatibility across the OpenResty image startup behavior and Railway's full allowed `PORT` range.
