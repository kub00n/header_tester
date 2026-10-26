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

## Native TLS on a public VPS (Python only)

The Python runtime can terminate TLS directly: `Cloudflare -> HTTPS -> Python`, without an intermediate HTTP proxy.
Run on an unprivileged port with an externally provisioned PEM certificate chain and matching unencrypted private key:

```sh
python3 app.py --port 8443 --tls-cert /etc/header-tester/certs/fullchain.pem \
  --tls-key /etc/header-tester/certs/private-key.pem
```

Alternatively, set `TLS_CERT_FILE` and `TLS_KEY_FILE`. Each CLI option overrides its corresponding environment value.
Both files must be configured together and non-empty; unreadable, invalid, or mismatched files fail safely at startup.
Encrypted private keys are unsupported and never trigger a password prompt. Files are loaded before the event loop
starts; restart the process after certificate renewal. No certificate or private key should be committed or baked into
the image.

The existing default container entry point reads these environment variables without a start-command override:

```sh
docker run --rm -p 443:8443 -e PORT=8443 -e HEADER_SERVER=python \
  -e TLS_CERT_FILE=/certs/fullchain.pem -e TLS_KEY_FILE=/certs/private-key.pem \
  --mount type=bind,src=/etc/header-tester/certs,dst=/certs,readonly raw-header-tester
```

The certificate directory must already exist on the host. The read-only mount keeps credentials outside the image;
restrict host file permissions appropriately. This maps public host port 443 to container port 8443. For direct Python
execution, arrange an equivalent host-level port mapping if Cloudflare connects on 443. The process opens one listener:
HTTP **or** HTTPS, not both. TLS does not change the default port: it remains 8080 unless `--port` or `PORT` is
configured.
Without TLS settings, plain HTTP and Railway defaults remain unchanged. OpenResty mode does not implement native TLS
here and rejects either TLS environment variable, including empty values, rather than silently ignoring it.

For Cloudflare deployment:

1. Create a proxied (orange-cloud) DNS `A` record for the hostname pointing to the VPS public IPv4 address.
2. Install a certificate valid for that hostname: either a Cloudflare Origin CA certificate or a publicly trusted
   certificate with its required chain. Keep the matching private key on the VPS.
3. Use Cloudflare **Full (strict)** encryption for this hostname. Do not downgrade the global SSL mode to bypass
   certificate failures. Origin CA certificates are trusted by Cloudflare, not ordinary browsers or default clients;
   direct verification requires explicitly trusting the Origin CA root. Publicly trusted certificates allow normal
   direct-client verification.
4. Configure the Cloudflare-to-origin connection to use HTTP/1.1 only (disable HTTP/2 to Origin if enabled). The Python
   TLS listener advertises only `http/1.1` via ALPN, requires TLS 1.2 or newer, and does not implement HTTP/2 or HTTP/3.
5. Restrict inbound origin HTTPS access to Cloudflare's published IP ranges in the VPS firewall, including IPv6 if
   applicable; keep those ranges current and retain separately controlled administrative access. Account for Docker's
   published-port firewall rules so the container mapping does not bypass the restriction.

TLS preserves the raw **decrypted HTTP/1.x header bytes received from Cloudflare**, not TLS ciphertext or the original
client-to-Cloudflare bytes. Cloudflare can still transform headers before forwarding them. This is a diagnostic echo
endpoint: never send sensitive request data, authentication headers, cookies, or personal information to it.
TLS handshake and shutdown each have a five-second timeout, separate from the existing request-header timeout.

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

Optional native TLS removes a header-rewriting HTTP proxy from the VPS origin path while leaving the raw byte-slicing
logic unchanged. Synchronous credential loading makes invalid TLS configuration fail before accepting connections;
bounded asynchronous handshakes and shutdowns limit stalled TLS connections. Explicit Python-only validation avoids
accidentally exposing plain HTTP when TLS was requested in OpenResty mode. Keeping TLS opt-in preserves deployments
that already terminate HTTPS at Railway or another edge.

Docker is now used because Railpack does not natively provision the official OpenResty runtime. One pinned Bookworm
image can contain OpenResty and the dependency-free Python implementation, while a small validated entry point selects
the runtime without duplicating deployment configuration. A small initial Nginx buffer avoids reserving 16 KiB for
ordinary requests, while one bounded large buffer handles request heads near the service limit. Keeping Python as the
default preserves existing behavior, and retaining `railpack.json` avoids removing the previous deployment metadata
even though Railway ignores it when the Dockerfile is present.

The container currently retains the official image's root execution model. Non-root execution was not enabled without
verified compatibility across the OpenResty image startup behavior and Railway's full allowed `PORT` range.
