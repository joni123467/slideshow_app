#!/usr/bin/env bash
set -eux

# === Konfiguration ===
ADMIN_USER="administrator"
# Ermittle Home-Verzeichnis des Admin-Users
ADMIN_HOME=$(getent passwd "$ADMIN_USER" | cut -d: -f6 || echo "/home/$ADMIN_USER")
INSTALL_DIR="$ADMIN_HOME/slideshow_app"

# === 1) Prüfe oder erstelle Admin-User ===
if ! id -u "$ADMIN_USER" > /dev/null 2>&1; then
  echo "Benutzer '$ADMIN_USER' existiert nicht. Erstelle ihn..."
  sudo useradd -m -s /bin/bash "$ADMIN_USER"
  sudo usermod -aG sudo "$ADMIN_USER"
  echo "Benutzer '$ADMIN_USER' angelegt mit Home '$ADMIN_HOME'."
else
  echo "Benutzer '$ADMIN_USER' existiert bereits."
fi

# Stelle sicher, dass Home existiert
sudo mkdir -p "$ADMIN_HOME"
sudo chown "$ADMIN_USER":"$ADMIN_USER" "$ADMIN_HOME"

# === 2) System-Pakete installieren ===
sudo apt update
sudo apt install -y \
  git python3 python3-venv python3-pip python3-dev libpam0g-dev libsmbclient-dev build-essential

# === 3) Repo klonen oder aktualisieren ===
if [ ! -d "$INSTALL_DIR/.git" ]; then
  echo "Klonen Repo nach $INSTALL_DIR"
  sudo -u "$ADMIN_USER" git clone git@github.com:joni123467/slideshow_app.git "$INSTALL_DIR"
else
  echo "Update Repo in $INSTALL_DIR"
  cd "$INSTALL_DIR"
  sudo -u "$ADMIN_USER" git fetch --all
  sudo -u "$ADMIN_USER" git reset --hard origin/main
fi
cd "$INSTALL_DIR"

# === 4) Virtualenv anlegen und Dependencies installieren ===
if [ ! -d "venv" ]; then
  echo "Erstelle Virtualenv"
  sudo -u "$ADMIN_USER" python3 -m venv venv
fi
VENV="$INSTALL_DIR/venv/bin/python"
PIP="$INSTALL_DIR/venv/bin/pip"
# Upgrade pip und installiere requirements
sudo -u "$ADMIN_USER" "$PIP" install --upgrade pip
if [ -f requirements.txt ]; then
  sudo -u "$ADMIN_USER" "$PIP" install -r requirements.txt
fi

# === 5) Helper-Skripte deployen ===
sudo mkdir -p /usr/local/bin
for helper in helpers/update_hostname.sh helpers/update_network_config.sh; do
  if [ -f "$INSTALL_DIR/$helper" ]; then
    sudo cp "$INSTALL_DIR/$helper" /usr/local/bin/
    sudo chmod +x "/usr/local/bin/$(basename $helper)"
  fi
done

# === 6) Service-Unit-Files deployen ===
sudo mkdir -p /etc/systemd/system
for svc in slideshow app; do
  unit_file="systemd/${svc}.service"
  if [ -f "$INSTALL_DIR/$unit_file" ]; then
    sudo cp "$INSTALL_DIR/$unit_file" /etc/systemd/system/
  fi
done

# === 7) Sudoers-Eintrag für Update-Script ===
SUDOERS_FILE=/etc/sudoers.d/slideshow_app
if [ ! -f "$SUDOERS_FILE" ]; then
  sudo tee "$SUDOERS_FILE" > /dev/null <<EOF
$ADMIN_USER ALL=(ALL) NOPASSWD: \
  /bin/cp $INSTALL_DIR/systemd/slideshow.service /etc/systemd/system/slideshow.service, \
  /bin/cp $INSTALL_DIR/systemd/app.service /etc/systemd/system/app.service, \
  /bin/cp $INSTALL_DIR/helpers/update_hostname.sh /usr/local/bin/update_hostname.sh, \
  /bin/cp $INSTALL_DIR/helpers/update_network_config.sh /usr/local/bin/update_network_config.sh, \
  /usr/bin/systemctl daemon-reload, \
  /usr/bin/systemctl restart slideshow.service, \
  /usr/bin/systemctl start slideshow.service, \
  /usr/bin/systemctl restart app.service, \
  /usr/bin/systemctl start app.service, \
  /bin/chmod +x /usr/local/bin/update_hostname.sh, \
  /bin/chmod +x /usr/local/bin/update_network_config.sh
EOF
  sudo chmod 440 "$SUDOERS_FILE"
fi

# === 8) systemd neu laden und Dienste starten ===
sudo systemctl daemon-reload
sudo systemctl enable slideshow.service
sudo systemctl enable app.service
sudo systemctl restart slideshow.service
sudo systemctl restart app.service

echo "=== Installation/Update abgeschlossen – Slideshow-App läuft unter $INSTALL_DIR ==="
