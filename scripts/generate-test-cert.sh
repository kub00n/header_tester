#!/bin/sh
# Generate diagnostic credentials only when explicitly invoked by the operator.
set -eu
LC_ALL=C
export LC_ALL
umask 077

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

[ "$#" -eq 1 ] || fail 'Usage: ./scripts/generate-test-cert.sh HOSTNAME'
hostname=$1
[ "${#hostname}" -le 64 ] || fail 'Hostname must be 1 to 64 ASCII characters'
case "$hostname" in
    ''|*[!a-zA-Z0-9.-]*|.*|*.|*..*) fail 'Invalid DNS hostname' ;;
esac
# Disable globbing before splitting DNS labels; reject subject/SAN syntax and options.
set -f
IFS=.
set -- $hostname
unset IFS
for label do
    case "$label" in
        ''|-*|*-) fail 'Invalid DNS hostname label' ;;
    esac
    [ "${#label}" -le 63 ] || fail 'DNS hostname labels must be at most 63 characters'
done
command -v openssl >/dev/null 2>&1 || fail 'OpenSSL must already be installed (1.1.1 or newer)'

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd -- "$project_root"
# mkdir is the exclusive reservation: existing directories, files and symlinks all fail.
# Never remove this directory, including after partial OpenSSL failure.
mkdir -m 700 certificates 2>/dev/null || fail 'Refusing existing certificates or unable to create it'
if ! openssl req -x509 -newkey rsa:2048 -sha256 -nodes -batch -days 7 \
    -subj "/CN=$hostname" -addext "subjectAltName=DNS:$hostname" \
    -keyout certificates/private-key.pem -out certificates/fullchain.pem >/dev/null 2>&1; then
    fail 'Test certificate generation failed; partial certificates directory is retained and will not be overwritten'
fi
printf '%s\n' 'Created a 7-day self-signed TEST certificate in certificates; not trusted by Cloudflare Full (strict).'
