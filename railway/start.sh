#!/bin/bash
# Railway entrypoint. Runs as root only long enough to hand the volume to
# pwuser (Railway mounts volumes root-owned), then starts the proxy relay and
# the server as pwuser under tini, like the stock image does.
set -euo pipefail

DATA=/home/pwuser/.linkedin-mcp
install -d -m 0700 -o pwuser -g pwuser "$DATA"
chown -R pwuser:pwuser "$DATA"

export HOME=/home/pwuser
export FASTMCP_HOME="${FASTMCP_HOME:-$DATA/fastmcp}"
as_pwuser=(setpriv --reuid=pwuser --regid=pwuser --init-groups)

if [[ -n "${UPSTREAM_PROXY_USERNAME:-}" ]]; then
    relay_port="${RELAY_PORT:-18181}"
    (while true; do
        RELAY_PORT="$relay_port" "${as_pwuser[@]}" python /opt/railway/auth_relay.py || true
        echo "auth relay exited; restarting" >&2
        sleep 1
    done) &
    export PROXY_SERVER="http://127.0.0.1:${relay_port}"
fi

if [[ "${LINKEDIN_LOGIN:-0}" == "1" ]]; then
    # One-off sign-in: noVNC viewer on 6080. Set PORT=6080 on Railway while
    # this runs so the public domain reaches it.
    cmd=(python -m linkedin_mcp_server --login --login-viewer)
else
    cmd=(python -m linkedin_mcp_server.remote_main)
fi

exec tini -e 143 -- "${as_pwuser[@]}" linkedin-mcp-entrypoint "${cmd[@]}"
