#!/usr/bin/env bash
set -e

echo "⚠️  WARNING: This will DELETE ALL Docker containers, images, volumes, and networks."
read -p "Are you sure you want to continue? [y/N] " confirm

if [[ "$confirm" =~ ^[Yy]$ ]]; then
    echo "Stopping all containers..."
    docker stop $(docker ps -q) 2>/dev/null || true

    echo "Removing all containers..."
    docker rm $(docker ps -aq) 2>/dev/null || true

    echo "Removing all images..."
    docker rmi $(docker images -aq) 2>/dev/null || true

    echo "Removing all volumes..."
    docker volume rm $(docker volume ls -q) 2>/dev/null || true

    echo "Removing all networks..."
    docker network rm $(docker network ls -q) 2>/dev/null || true

    echo "✅ Docker cleanup complete."
else
    echo "Aborted. Nothing was deleted."
fi
