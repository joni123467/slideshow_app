#!/usr/bin/env bash
set -eux

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

echo "=== Update startet: $(date) ==="

# 1) Repo updaten
git fetch --all
git reset --hard origin/main

# 2) Virtualenv anlegen/installieren
if [ ! -d venv ]; then
  python3 -m venv venv
fi
VENV_PIP="$BASE_DIR/venv/bin/pip"
if [ -f requirements.txt ]; then
  $VENV_PIP install --upgrade pip
  $VENV_PIP install -r requirements.txt
fi

# 3) Service-Unit-Dateien aus dem Repo ins systemd-Verzeichnis kopieren
#    (nur, wenn sie sich verändert haben)
for svcfile in slideshow.service app.service; do
  if [ -f "$BASE_DIR/$svcfile" ]; then
    echo "Kopiere $svcfile nach /etc/systemd/system/"
    sudo cp "$BASE_DIR/$svcfile" /etc/systemd/system/
  fi
done

# 4) systemd neu laden, damit neue/angepasste Units wirksam werden
echo "Reload systemd daemon"
sudo systemctl daemon-reload

# 5) Dienste (neu)starten
for svc in slideshow app; do
  unit="${svc}.service"
  if systemctl is-active --quiet "$unit"; then
    echo "Restarting $unit..."
    sudo systemctl restart "$unit"
  else
    echo "Starting $unit..."
    sudo systemctl start "$unit"
  fi
done

echo "=== Update beendet: $(date) ==="

