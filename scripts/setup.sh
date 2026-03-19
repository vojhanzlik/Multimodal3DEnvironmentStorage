#!/usr/bin/env bash
# Start external services required by robot_manager.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Starting ReductStore via Docker Compose..."
docker compose -f "$PROJECT_DIR/docker-compose.yml" up -d

# Wait for ReductStore to become healthy
echo -n "Waiting for ReductStore on http://127.0.0.1:8383 "
for i in $(seq 1 30); do
    if curl -s --max-time 1 http://127.0.0.1:8383/api/v1/info > /dev/null 2>&1; then
        echo " ready."
        exit 0
    fi
    echo -n "."
    sleep 1
done

echo " TIMEOUT — ReductStore did not start within 30 seconds."
exit 1
