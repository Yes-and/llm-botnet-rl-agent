#!/bin/sh
set -e
# Same flags previously passed via compose's `command:` — hardcoded here now that
# startup also needs to load the pre-fetched image once the daemon is ready.
dockerd-entrypoint.sh dockerd --host=tcp://0.0.0.0:2375 --host=unix:///var/run/docker.sock &
DOCKERD_PID=$!

until docker version >/dev/null 2>&1; do sleep 0.5; done
docker load -i /alpine.tar

wait $DOCKERD_PID
