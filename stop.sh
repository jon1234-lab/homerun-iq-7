#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "Stopping HomerunIQ..."
docker compose down
echo "Stopped. Run ./start.sh any time to launch it again."
read -p "Press Enter to close..."
