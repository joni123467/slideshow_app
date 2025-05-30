#!/usr/bin/env bash
set -eux

# --- Prüfe oder erstelle Benutzer 'administrator' ---
if ! id -u administrator > /dev/null 2>&1; then
  echo "Benutzer 'administrator' existiert nicht. Erstelle ihn..."
  # Benutzer mit Home-Verzeichnis und Bash-Shell anlegen
  sudo useradd -m -s /bin/bash administrator
  # In sudo-Gruppe aufnehmen für erhöhte Rechte
  sudo usermod -aG sudo administrator
  echo "Benutzer 'administrator' angelegt."
else
  echo "Benutzer 'administrator' existiert bereits."
fi

# --- Installation System-Pakete ---
sudo apt update
sudo apt install -y \
  git python3 python3-venv python3-pip python3-dev libpam0g-dev libsmbclient-dev \
  build-essential

# --- Repo klonen ---
cd ~
if [ ! -d slideshow_app ]; then
  git clone git@github.com:joni123467/slideshow_app.git
fi
cd slideshow_app

# --- Virtualenv anlegen & Dependencies installieren ---
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# --- Skript ausführbar machen ---
chmod +x update.sh

# --- Deploy Helper-Skripte ---
sudo mkdir -p /usr/local/bin
sudo cp helpers/update_hostname.sh       /usr/local/bin/
sudo cp helpers/update_network_config.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/update_hostname.sh
sudo chmod +x /usr/local/bin/update_network_config.sh

# --- Deploy systemd-Service-Units ---
sudo mkdir -p /etc/systemd/system
sudo cp systemd/slideshow.service /etc/systemd/system/
sudo cp systemd/app.service       /etc/systemd/system/

# --- Sudoers-Eintrag für Update-Script (falls noch nicht vorhanden) ---
SUDOERS_FILE=/etc/sudoers.d/slideshow_app
if [ ! -f "$SUDOERS_FILE" ]; then
  sudo tee "$SUDOERS_FILE" > /dev/null << 'EOF'
administrator ALL=(ALL) NOPASSWD: \
  /bin/cp /home/administrator/slideshow_app/systemd/slideshow.service /etc/systemd/system/slideshow.service, \
  /bin/cp /home/administrator/slideshow_app/systemd/app.service /etc/systemd/system/app.service, \
  /bin/cp /home/administrator/slideshow_app/helpers/update_hostname.sh /usr/local/bin/update_hostname.sh, \
  /bin/cp /home/administrator/slideshow_app/helpers/update_network_config.sh /usr/local/bin/update_network_config.sh, \
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

# --- systemd neu laden und Dienste aktivieren/starten ---
sudo systemctl daemon-reload
sudo systemctl enable slideshow.service
sudo systemctl enable app.service
sudo systemctl restart slideshow.service
sudo systemctl restart app.service

echo "=== Installation abgeschlossen! Slideshow-App ist startbereit ==="
