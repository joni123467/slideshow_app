#!/usr/bin/env bash
set -eux

# =============================================================================
# Slideshow-App Installationsskript (immer neuestes Release-Tag installieren)
# =============================================================================

# 1) Basis-Konfiguration
ADMIN_USER="administrator"
# Ermittel Home-Verzeichnis des Admin-User (Fallback /home/administrator)
ADMIN_HOME=$(getent passwd "$ADMIN_USER" | cut -d: -f6 || echo "/home/$ADMIN_USER")
INSTALL_DIR="$ADMIN_HOME/slideshow_app"
REPO_URL="https://github.com/joni123467/slideshow_app.git"

# 2) Prüfe oder lege Admin-User an
if ! id -u "$ADMIN_USER" > /dev/null 2>&1; then
  echo "Benutzer '$ADMIN_USER' existiert nicht. Erstelle ihn..."
  sudo useradd -m -s /bin/bash "$ADMIN_USER"
  sudo usermod -aG sudo "$ADMIN_USER"
  echo "Benutzer '$ADMIN_USER' angelegt mit Home-Verzeichnis '$ADMIN_HOME'."
else
  echo "Benutzer '$ADMIN_USER' existiert bereits."
fi

# Sicherstellen, dass Home-Verzeichnis existiert und dem Admin gehört
sudo mkdir -p "$ADMIN_HOME"
sudo chown "$ADMIN_USER":"$ADMIN_USER" "$ADMIN_HOME"

# 3) System-Pakete installieren
echo "Installiere System-Pakete..."
sudo apt update
sudo apt install -y \
  git \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  libpam0g-dev \
  libsmbclient-dev \
  build-essential \
  jq

# 4) Repo klonen oder aktualisieren
if [ ! -d "$INSTALL_DIR/.git" ]; then
  echo "Klonen des Repositories nach $INSTALL_DIR"
  sudo -u "$ADMIN_USER" git clone "$REPO_URL" "$INSTALL_DIR"
else
  echo "Aktualisiere Repo in $INSTALL_DIR"
  cd "$INSTALL_DIR"
  sudo -u "$ADMIN_USER" git fetch --all --tags
  sudo -u "$ADMIN_USER" git reset --hard origin/main
fi

cd "$INSTALL_DIR"

# 5) Neuestes Git-Tag ermitteln
echo "Ermittle neuestes Release-Tag..."
# Stelle sicher, dass alle Tags lokal vorhanden sind
sudo -u "$ADMIN_USER" git fetch --all --tags
# Verwende semver-sort und wähle das oberste
LATEST_TAG=$(sudo -u "$ADMIN_USER" git tag -l --sort=-version:refname | head -n1)
if [ -z "$LATEST_TAG" ]; then
  echo "   Keine Tags im Repo gefunden. Nutze 'main'."
  RELEASE_REF="main"
else
  RELEASE_REF="$LATEST_TAG"
  echo "   Neuestes Release-Tag: $RELEASE_REF"
fi

# 6) Checkout des Release-Tags oder main
if [ "$RELEASE_REF" = "main" ]; then
  echo "Checke Branch 'main' aus"
  sudo -u "$ADMIN_USER" git checkout main -f
  sudo -u "$ADMIN_USER" git pull origin main
else
  echo "Checke Tag '$RELEASE_REF' aus (detached HEAD)"
  sudo -u "$ADMIN_USER" git checkout "tags/$RELEASE_REF" -f
fi

# 7) Virtualenv anlegen & Dependencies installieren
if [ ! -d "venv" ]; then
  echo "Erstelle Virtualenv im Verzeichnis $INSTALL_DIR/venv"
  sudo -u "$ADMIN_USER" python3 -m venv venv
fi

PIP="$INSTALL_DIR/venv/bin/pip"
echo "Upgrade pip und installiere Python-Abhängigkeiten"
sudo -u "$ADMIN_USER" "$PIP" install --upgrade pip
if [ -f requirements.txt ]; then
  sudo -u "$ADMIN_USER" "$PIP" install -r requirements.txt
fi

# 8) Helper-Skripte deployen
echo "Deploye Helper-Skripte nach /usr/local/bin"
sudo mkdir -p /usr/local/bin
for helper in helpers/update_hostname.sh helpers/update_network_config.sh; do
  if [ -f "$INSTALL_DIR/$helper" ]; then
    sudo cp "$INSTALL_DIR/$helper" /usr/local/bin/
    sudo chmod +x "/usr/local/bin/$(basename "$helper")"
  fi
done

# 9) systemd-Unit-Dateien deployen
echo "Deploye systemd-Unit-Dateien nach /etc/systemd/system"
sudo mkdir -p /etc/systemd/system
for svc in slideshow app; do
  unit_file="systemd/${svc}.service"
  if [ -f "$INSTALL_DIR/$unit_file" ]; then
    sudo cp "$INSTALL_DIR/$unit_file" /etc/systemd/system/
  fi
done

# 10) Sudoers-Eintrag für Update/Deploy sicherstellen
SUDOERS_FILE=/etc/sudoers.d/slideshow_app
if [ ! -f "$SUDOERS_FILE" ]; then
  echo "Lege /etc/sudoers.d/slideshow_app an"
  sudo tee "$SUDOERS_FILE" > /dev/null <<EOF
administrator ALL=(ALL) NOPASSWD: \\
  /bin/cp $INSTALL_DIR/systemd/slideshow.service /etc/systemd/system/slideshow.service, \\
  /bin/cp $INSTALL_DIR/systemd/app.service /etc/systemd/system/app.service, \\
  /bin/cp $INSTALL_DIR/helpers/update_hostname.sh /usr/local/bin/update_hostname.sh, \\
  /bin/cp $INSTALL_DIR/helpers/update_network_config.sh /usr/local/bin/update_network_config.sh, \\
  /usr/bin/systemctl daemon-reload, \\
  /usr/bin/systemctl restart slideshow.service, \\
  /usr/bin/systemctl start slideshow.service, \\
  /usr/bin/systemctl restart app.service, \\
  /usr/bin/systemctl start app.service, \\
  /bin/chmod +x /usr/local/bin/update_hostname.sh, \\
  /bin/chmod +x /usr/local/bin/update_network_config.sh
EOF
  sudo chmod 440 "$SUDOERS_FILE"
else
  echo "Sudoers-Eintrag /etc/sudoers.d/slideshow_app existiert bereits"
fi

# 11) systemd neu laden & Dienste aktivieren/starten
echo "Systemd daemon neu laden und Dienste aktivieren/starten"
sudo systemctl daemon-reload
sudo systemctl enable slideshow.service
sudo systemctl enable app.service
sudo systemctl restart slideshow.service
sudo systemctl restart app.service

echo "=== Installation/Update abgeschlossen – Slideshow-App läuft unter $INSTALL_DIR, Release: $RELEASE_REF ==="

