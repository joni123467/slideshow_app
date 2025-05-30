#!/usr/bin/env bash
set -eux

# 1) System-Pakete installieren
sudo apt update
sudo apt install -y \
  git python3 python3-venv python3-pip python3-dev libpam0g-dev libsmbclient-dev \
  build-essential

# 2) Repo klonen
cd ~
if [ ! -d slideshow_app ]; then
  git clone https://github.com/joni123467/slideshow_app.git
fi
cd slideshow_app

# 3) Virtualenv anlegen & Dependencies installieren
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 4) update.sh ausführbar machen
chmod +x update.sh

# 5) Helper-Skripte und systemd-Units deployen
#    (deinen update.sh kannst Du jetzt auch direkt aufrufen)
sudo mkdir -p /etc/systemd/system
sudo mkdir -p /usr/local/bin
# Copy units
sudo cp systemd/slideshow.service /etc/systemd/system/
sudo cp systemd/app.service       /etc/systemd/system/
# Copy helpers
sudo cp helpers/update_hostname.sh       /usr/local/bin/
sudo cp helpers/update_network_config.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/update_*.sh

# 6) Sudoers‐Eintrag anlegen (für späteres update.sh)
sudo tee /etc/sudoers.d/slideshow_app > /dev/null <<'EOF'
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
sudo chmod 440 /etc/sudoers.d/slideshow_app

# 7) systemd neu laden & Services aktivieren/starten
sudo systemctl daemon-reload
sudo systemctl enable slideshow.service
sudo systemctl enable app.service
sudo systemctl restart slideshow.service
sudo systemctl restart app.service

echo "=== Installation abgeschlossen! Deine Slideshow-App läuft jetzt ==="

