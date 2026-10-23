# Raw Header Tester

A minimal, standard-library HTTP service that returns the raw header lines received by the Python process. The
response is plain text, not JSON. Header order, duplicate names, casing, whitespace, and CRLF endings are retained.

## Local use

Run the service with Python 3.11 or newer:

```sh
python3 app.py --port 8080
```

When using uv locally, the equivalent command is:

```sh
uv run python app.py --port 8080
```

The optional `--port` argument takes precedence over `PORT`, and the service defaults to port 8080 when neither is
set. `PORT` remains supported so Railway can provide the listening port automatically.

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

Create a Railway service from this repository and use Railpack as the builder. Railpack detects uv from
`pyproject.toml` and `uv.lock`, then installs the locked environment with `uv sync --locked`. The project is configured
as a non-package application because there is no Python package to install. Railpack selects its supported uv tooling;
it does not necessarily use the uv version that generated the lock file locally or the globally latest release.

The `railpack.json` file keeps the start command as `python3 app.py`. Railpack places the uv-created virtual
environment on `PATH`, so invoking uv at runtime is unnecessary. Railway supplies `PORT`. Generate a public domain in
the service networking settings; no start-command override is needed.

Compare Railway directly with a hostname proxied by Cloudflare:

```sh
curl --http1.1 --raw -H 'X-Route: first' -H 'X-Route: second' https://your-service.up.railway.app/
curl --http1.1 --raw -H 'X-Route: first' -H 'X-Route: second' https://headers.example.com/
```

Cloudflare, Railway's edge, and other proxies may normalize, combine, remove, add, or rebuild headers before the
request reaches this process. The service can report only the bytes delivered to its TCP socket, not the original
client-to-edge bytes. Railway's public URL may also traverse infrastructure that changes the request.

## Limits and errors

The server accepts one HTTP/1.0 or HTTP/1.1 request per connection. It allows at most 16 KiB through the terminating
`CRLFCRLF` and waits up to five seconds for that boundary. Incomplete or malformed requests receive `400`, timeouts
receive `408`, and oversized request heads receive `431`. Error bodies are fixed and never reflect partial input.

Run the standard-library tests with:

```sh
python3 -m unittest discover -v
```

## Reasoning

High-level HTTP servers parse headers into mappings or normalized field objects, which can lose ordering, duplicate
fields, original casing, whitespace, and wire line endings. Reading the request head directly with
`asyncio.start_server` preserves the byte slice that reaches the process while still enforcing strict size and time
limits. This makes direct and proxied requests easier to compare without pretending to observe bytes changed upstream.

The locked, dependency-free uv environment gives Railpack a reproducible build while the minimal configuration keeps
the process entry point explicit and reviewable.
