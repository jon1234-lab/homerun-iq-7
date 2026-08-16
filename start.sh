#!/usr/bin/env bash
# Double-click this file (or run ./start.sh) to launch HomerunIQ.
# Requires only Docker Desktop -- no Python, no Node.js, no setup.

set -e
cd "$(dirname "$0")"

echo "⚾ Starting HomerunIQ..."

if ! command -v docker &> /dev/null; then
  echo ""
  echo "Docker was not found on this computer."
  echo "Install Docker Desktop first: https://www.docker.com/products/docker-desktop/"
  read -p "Press Enter to close..."
  exit 1
fi

if ! docker info &> /dev/null; then
  echo ""
  echo "Docker is installed but not running."
  echo "Open Docker Desktop, wait until it says 'Engine running', then re-run this script."
  read -p "Press Enter to close..."
  exit 1
fi

[ -f backend/.env ] || cp backend/.env.example backend/.env
[ -f frontend/.env.local ] || cp frontend/.env.example frontend/.env.local

echo "Building and starting containers (first run takes a few minutes)..."
docker compose up --build -d

echo "Waiting for the app to come online..."
for i in $(seq 1 90); do
  if curl -s http://localhost:8000/health > /dev/null 2>&1; then break; fi
  sleep 2
done

echo ""
echo "✅ HomerunIQ is running!"
echo "   Dashboard: http://localhost:3000"
echo "   API docs:  http://localhost:8000/docs"
echo ""
echo "Note: the first page load pulls live rosters and Statcast data,"
echo "so it can take 10-30 seconds. After that it's cached and fast."
echo ""

if command -v open &> /dev/null; then open http://localhost:3000
elif command -v xdg-open &> /dev/null; then xdg-open http://localhost:3000; fi

echo "To stop later, run ./stop.sh"
read -p "Press Enter to close this window..."
