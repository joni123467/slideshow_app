#!/usr/bin/env bash
set -eux

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# === Sudoers-Einträge sicherstellen ===
SUDOERS_FILE=/etc/sudoers.d/slideshow_app
if ! sudo test -f "$SUDOERS_FILE"; then
  echo "-> Erstelle Sudoers-Eintrag für automatisches Update" 
  sudo tee "$SUDOERS_FILE" > /dev/null <<'EOF'
# Erlaubt Kopieren der systemd-Units
administrator ALL=(ALL) NOPASSWD: /bin/cp /home/administrator/slideshow_app/systemd/slideshow.service /etc/systemd/system/slideshow.service
administrator ALL=(ALL) NOPASSWD: /bin/cp /home/administrator/slideshow_app/systemd/app.service /etc/systemd/system/app.service

# Erlaubt Kopieren der Helper-Skripte
administrator ALL=(ALL) NOPASSWD: /bin/cp /home/administrator/slideshow_app/helpers/update_hostname.sh /usr/local/bin/update_hostname.sh
administrator ALL=(ALL) NOPASSWD: /bin/cp /home/administrator/slideshow_app/helpers/update_network_config.sh /usr/local/bin/update_network_config.sh

# Erlaubt systemd-Reload und Restart/Start der beiden Services
administrator ALL=(ALL) NOPASSWD: /usr/bin/systemctl daemon-reload
administrator ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart slideshow.service
administrator ALL=(ALL) NOPASSWD: /usr/bin/systemctl start slideshow.service
administrator ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart app.service
administrator ALL=(ALL) NOPASSWD: /usr/bin/systemctl start app.service

# Erlaubt chmod +x der Helper-Skripte
administrator ALL=(ALL) NOPASSWD: /bin/chmod +x /usr/local/bin/update_hostname.sh
administrator ALL=(ALL) NOPASSWD: /bin/chmod +x /usr/local/bin/update_network_config.sh
EOF
  sudo chmod 440 "$SUDOERS_FILE"
else
  echo "-> Sudoers-Eintrag bereits vorhanden"
fi

LOGFILE="$BASE_DIR/update.log"
echo "" >> "$LOGFILE"
echo "=== Update startet: $(date '+%Y-%m-%d %H:%M:%S') ===" | tee -a "$LOGFILE"

# 1) Repo updaten
echo "-> Git fetch & reset" | tee -a "$LOGFILE"
git fetch --all | tee -a "$LOGFILE"
git reset --hard origin/main | tee -a "$LOGFILE"

# 2) Virtualenv anlegen/installieren
echo "-> Virtualenv prüfen/erstellen" | tee -a "$LOGFILE"
if [ ! -d venv ]; then
  python3 -m venv venv | tee -a "$LOGFILE"
  echo "   Virtualenv angelegt" | tee -a "$LOGFILE"
else
  echo "   Virtualenv existiert" | tee -a "$LOGFILE"
fi

VENV_PIP="$BASE_DIR/venv/bin/pip"
# 3) Dependencies installieren
echo "-> Installiere Dependencies" | tee -a "$LOGFILE"
if [ -f requirements.txt ]; then
  $VENV_PIP install --upgrade pip | tee -a "$LOGFILE"
  $VENV_PIP install -r requirements.txt | tee -a "$LOGFILE"
else
  echo "   Keine requirements.txt gefunden, überspringe" | tee -a "$LOGFILE"
fi

# 4) Service-Unit- und Helper-Skripte deployen
echo "-> Deploy Service-Units" | tee -a "$LOGFILE"
for svcfile in slideshow.service app.service; do
  if [ -f "$BASE_DIR/systemd/$svcfile" ]; then
    echo "   Kopiere $svcfile" | tee -a "$LOGFILE"
    sudo cp "$BASE_DIR/systemd/$svcfile" /etc/systemd/system/ | tee -a "$LOGFILE"
  fi
done

echo "-> Deploy Helper-Skripte" | tee -a "$LOGFILE"
for helper in helpers/update_hostname.sh helpers/update_network_config.sh; do
  if [ -f "$BASE_DIR/$helper" ]; then
    echo "   Kopiere $helper" | tee -a "$LOGFILE"
    sudo cp "$BASE_DIR/$helper" /usr/local/bin/ | tee -a "$LOGFILE"
    sudo chmod +x "/usr/local/bin/$(basename $helper)" | tee -a "$LOGFILE"
  fi
done

# 5) systemd neu laden & Dienste neu starten
echo "-> Reload systemd daemon" | tee -a "$LOGFILE"
sudo systemctl daemon-reload | tee -a "$LOGFILE"

echo "-> Restart Services" | tee -a "$LOGFILE"
for svc in slideshow app; do
  unit="${svc}.service"
  if systemctl is-active --quiet "$unit"; then
    echo "   Restart $unit" | tee -a "$LOGFILE"
    sudo systemctl restart "$unit" | tee -a "$LOGFILE"
  else
    echo "   Start $unit" | tee- a "$LOGFILE"
    sudo systemctl start "$unit" | tee -a "$LOGFILE"
  fi
done

echo "=== Update beendet: $(date '+%Y-%m-%d %H:%M:%S') ===" | tee -a "$LOGFILE"