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

## Docker Compose: HTTP and HTTPS on a VPS

Prerequisites: Docker Engine and a current Docker Compose plugin on the VPS, available host ports 80 and 443, and
a checkout containing the new commits that add `compose.yaml` and these deployment instructions. The existing image
includes Python 3.11; no host Python 3.11 installation is needed. Initial builds require network access to obtain the
base image and its OS packages.

### Self-signed diagnostic certificate

For a direct diagnostic test, use the installed OpenSSL CLI (1.1.1 or newer) on the VPS. From the repository root:

```sh
./scripts/generate-test-cert.sh headers.example.com
docker compose up -d --build
docker compose logs --tail=100 http https
```

The script accepts exactly one DNS hostname (ASCII letters, digits, hyphens, dots; at most 64 characters, labels at
most 63 characters, no leading/trailing label hyphens). It generates a matching RSA private key and a seven-day
self-signed **TEST** certificate with that hostname as the CN and DNS SAN. There is no automatic startup generation.
`certificates/private-key.pem` and `certificates/fullchain.pem` are untracked.
The entire `certificates/` directory is ignored by Git and Docker.
Unset any previously exported `HOST_TLS_CERT_FILE` and `HOST_TLS_KEY_FILE` to use these defaults.

Generation uses umask `077`, creates new directories with mode `700`, and produces files accessible only to the owner.
The script atomically reserves `certificates/` and refuses any existing path there, even an empty directory, symlink, or
partial previous output. It never overwrites files, prints key material, or deletes partial output on failure.
Have the operator review failed output separately; rerunning does not replace or repair it. Run in an owner-controlled
checkout, not a directory writable by untrusted users. Existing directory permissions are not changed.

For direct verification, use the matching hostname, bypassing Cloudflare with a local address if testing on the VPS:

```sh
curl --http1.1 --cacert certificates/fullchain.pem \
  --resolve headers.example.com:443:127.0.0.1 https://headers.example.com/
```

`curl -k` is an alternative **only for a local diagnostic test**; it disables certificate verification.
This self-signed certificate is **not Cloudflare Origin CA** and is **not compatible with Cloudflare Full (strict)**.
For Full (strict), provision a valid Cloudflare Origin CA or publicly trusted certificate separately as described below.
Never downgrade the global Cloudflare encryption mode to accommodate a test certificate.

### Cloudflare Origin CA or publicly trusted certificate

For a Cloudflare-proxied hostname, open the zone in the Cloudflare dashboard and go to
**SSL/TLS -> Origin Server -> Create Certificate**. Choose to generate a private key and CSR with Cloudflare,
include the intended hostname, choose validity, and create the certificate in PEM format. Save the displayed private
key securely before leaving the screen; it cannot be viewed again afterward.

For a fresh checkout without `certificates/`, manually create the dedicated directory with restrictive permissions:

```sh
umask 077
mkdir -m 700 certificates
```

Manually save the Origin Certificate as `certificates/fullchain.pem` and its matching private key as
`certificates/private-key.pem`, with file permissions `600`. Keep both outside Git; the directory is also excluded
from the Docker build context. Do not overwrite any existing credentials or run the self-signed generator on this
already provisioned directory. With external overrides unset, run `docker compose up -d --build`, then configure
Cloudflare **Full (strict)**. Origin CA certificates are trusted by Cloudflare, not ordinary direct browsers.

For direct-browser trust, use a publicly trusted certificate such as **Let's Encrypt via DNS-01** instead. Provision
and renew it separately, including the required certificate chain, using either these dedicated files or external
overrides below. Never downgrade global Cloudflare TLS mode to bypass certificate validation.

### Externally provisioned certificate

Keep externally provisioned certificates and matching unencrypted keys outside the repository and build context.
Use absolute paths to existing, dedicated files on the Docker host, not directories. Override both default paths:

```sh
export HOST_TLS_CERT_FILE=/etc/header-tester/certs/fullchain.pem
export HOST_TLS_KEY_FILE=/etc/header-tester/certs/private-key.pem
docker compose up -d --build
docker compose logs --tail=100 http https
```

Unset or empty path variables fall back to `./certificates/fullchain.pem` and `./certificates/private-key.pem`.
The relative defaults resolve from the Compose project directory, not the invoking shell's current directory.
Missing host paths fail at container creation instead of being created as directories. Files are mounted read-only at
`/certs/fullchain.pem` and `/certs/private-key.pem` in the HTTPS container only. No credential contents are placed in
Compose or the image. `.dockerignore` excludes common environment and certificate/key artifacts as defense in depth;
the Dockerfile still copies only explicit application sources. Do not place credentials among those sources.

On AlmaLinux 8 with SELinux enforcing, the mounts use private `Z` relabeling. Use dedicated certificate/key files for
this HTTPS container: relabeling changes their host labels and can disrupt other services that share those files.
Do not point these mounts at shared system certificate files or directories. Have the host administrator provision
dedicated files with suitable permissions and labels; keep SELinux enforcing.

The `http` service publishes host **80 -> 8080** and the `https` service publishes **443 -> 8443**. Both explicitly
select Python through the existing image entry point; OpenResty is not started and there is no intermediate HTTP proxy.
HTTP receives no TLS variables or mounts, even if TLS variables are exported in the invoking shell. Both services
restart unless stopped. Railway and the standalone runtime selector remain unchanged.

Inspect `http://headers.example.com/` (port 80) and `https://headers.example.com/` (port 443), replacing the hostname
with yours. There is **no automatic HTTP-to-HTTPS redirect**: both listeners echo headers for inspection. Cloudflare
edge redirects, if enabled separately, may prevent HTTP requests from reaching the HTTP listener.

Apply the Cloudflare **Full (strict)**, HTTP/1.1 origin, and certificate-trust guidance below. Configure firewall access
for both published ports, including IPv6 where applicable. Docker's published-port rules can bypass ordinary host
firewall rules; enforce origin restrictions in rules that actually cover Docker traffic. For Cloudflare-only access,
allow Cloudflare's current published IP ranges on both 80 and 443 and retain separate administrative access.

After certificate renewal, recreate HTTPS so file bind mounts pick up files replaced atomically, and Python reloads TLS:

```sh
docker compose up -d --force-recreate https
docker compose logs --tail=100 https
```

Keep the exported paths available for subsequent Compose commands. The diagnostic endpoint must not receive sensitive
headers or personal data, especially over unencrypted HTTP.

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

Compose uses two Python processes because each opens only one listener. Reusing the existing image and entry point
adds simultaneous HTTP/HTTPS without a header-rewriting proxy or changes to Railway. Ignored local test credentials
make diagnostics reproducible; external overrides retain production certificate provisioning. Exclusive directory
creation prevents accidental replacement, while read-only mounts and build exclusions keep keys out of the image.

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
