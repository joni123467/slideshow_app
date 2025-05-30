#!/usr/bin/env bash
set -eux

# Basis-Verzeichnis der App
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Update startet: $(date) ==="

# 1) ins Repo-Verzeichnis
cd "$BASE_DIR"

# 2) neueste Änderungen holen und harten Reset auf origin/main
git fetch --all
git reset --hard origin/main

# 3) (optional) Python-Abhängigkeiten installieren
if [ -f requirements.txt ]; then
  pip3 install -r requirements.txt
fi

# 4) Neustart der Services (hier systemd-Service 'slideshow')
if systemctl is-active --quiet slideshow_app; then
  echo "Restarting slideshow.service..."
  sudo systemctl restart slideshow
else
  echo "Starting slideshow_app.service..."
  sudo systemctl start slideshow
fi

echo "=== Update beendet: $(date) ==="

