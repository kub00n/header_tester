# Raw Header Tester

A minimal, standard-library HTTP service that returns the raw header lines received by the Python process. The
response is plain text, not JSON. Header order, duplicate names, casing, whitespace, and CRLF endings are retained.

## Local use

Run the service with Python 3.11 or newer:

```sh
PORT=8080 python3 app.py
```

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

Create a Railway service from this repository. Railway detects the `Dockerfile`, builds it, and supplies `PORT` at
runtime. Generate a public domain in the service networking settings. No packages or start-command override are
needed.

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
# header_tester
# header_tester
# header_tester
