#!/bin/sh

set -eu

selector=${HEADER_SERVER-python}
port=${PORT-8080}
port_error='PORT must contain 1 to 20 digits and be between 1 and 65535'

case "$port" in
    ''|*[!0-9]*)
        printf '%s\n' "$port_error" >&2
        exit 2
        ;;
esac

# Bound validation work before removing accepted leading zeros.
if [ "${#port}" -gt 20 ]; then
    printf '%s\n' "$port_error" >&2
    exit 2
fi

# Normalize leading zeros before numeric comparisons and config rendering.
normalized_port=$port
while [ "${normalized_port#0}" != "$normalized_port" ]; do
    normalized_port=${normalized_port#0}
done
[ -n "$normalized_port" ] || normalized_port=0

if [ "${#normalized_port}" -gt 5 ] \
    || [ "$normalized_port" -lt 1 ] \
    || [ "$normalized_port" -gt 65535 ]; then
    printf '%s\n' "$port_error" >&2
    exit 2
fi

case "$selector" in
    python)
        exec python3 app.py
        ;;
    openresty)
        if [ "${TLS_CERT_FILE+x}" = x ] || [ "${TLS_KEY_FILE+x}" = x ]; then
            printf '%s\n' 'TLS_CERT_FILE and TLS_KEY_FILE are supported only with HEADER_SERVER=python' >&2
            exit 2
        fi
        script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
        template="$script_dir/openresty/nginx.conf.template"
        runtime_parent=${HEADER_SERVER_RUNTIME_PARENT-/tmp}

        if [ ! -d "$runtime_parent" ]; then
            printf '%s\n' 'HEADER_SERVER_RUNTIME_PARENT must be a directory under /tmp' >&2
            exit 2
        fi
        if ! runtime_parent=$(CDPATH= cd "$runtime_parent" && pwd -P); then
            printf '%s\n' 'HEADER_SERVER_RUNTIME_PARENT must be a directory under /tmp' >&2
            exit 2
        fi
        case "$runtime_parent" in
            /tmp|/tmp/*) ;;
            *)
                printf '%s\n' 'HEADER_SERVER_RUNTIME_PARENT must be a directory under /tmp' >&2
                exit 2
                ;;
        esac

        runtime_dir=$(mktemp -d "$runtime_parent/header-tester.XXXXXX")
        config="$runtime_dir/nginx.conf"

        sed "s/{{PORT}}/$normalized_port/g" "$template" >"$config"
        exec openresty -p "$runtime_dir/" -c "$config"
        ;;
    *)
        printf '%s\n' 'HEADER_SERVER must be python or openresty' >&2
        exit 2
        ;;
esac
