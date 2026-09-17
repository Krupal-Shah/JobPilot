#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"

# Supervise only our own children; never kill unrelated listeners on these ports.
app_pid=""
extension_pid=""
cleanup() {
    for server_pid in "$app_pid" "$extension_pid"; do
        if [ -n "$server_pid" ]; then
            kill "$server_pid" 2>/dev/null || true
            wait "$server_pid" 2>/dev/null || true
        fi
    done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Disable the development reloader so each child is one supervised process.
venv/bin/python -c 'from app import app; app.run(host="127.0.0.1", port=5050, debug=True, use_reloader=False)' &
app_pid=$!
venv/bin/python -c 'from extension_server import app; app.run(host="127.0.0.1", port=8421, debug=True, use_reloader=False)' &
extension_pid=$!
while kill -0 "$app_pid" 2>/dev/null && kill -0 "$extension_pid" 2>/dev/null; do
    sleep 1
done
# A development server exited unexpectedly; the EXIT trap stops the other one.
exit 1
