#!/usr/bin/env bash
set -eux

# =============================================================================
# Slideshow-App Installationsskript (inkl. config-Backup & Restore)
# =============================================================================

# 1) Basis-Konfiguration
ADMIN_USER="administrator"
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

sudo mkdir -p "$ADMIN_HOME"
sudo chown "$ADMIN_USER":"$ADMIN_USER" "$ADMIN_HOME"

# === NEU: config.json-Backup, falls alter Ordner vorhanden ===
CONFIG_BAK=""
if [ -d "$INSTALL_DIR" ]; then
  if [ -f "$INSTALL_DIR/config.json" ]; then
    CONFIG_BAK="/tmp/config.json.$(date +%s).bak"
    cp "$INSTALL_DIR/config.json" "$CONFIG_BAK"
    echo "Vorhandene config.json wurde nach $CONFIG_BAK gesichert."
  fi
  echo "Lösche alten Installationsordner $INSTALL_DIR"
  sudo rm -rf "$INSTALL_DIR"
fi

# 3) System-Pakete installieren (inklusive jq)
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

# 4) Repo klonen
echo "Klonen des Repositories nach $INSTALL_DIR"
sudo -u "$ADMIN_USER" git clone "$REPO_URL" "$INSTALL_DIR"

cd "$INSTALL_DIR"

# === NEU: config.json-Backup zurückspielen (noch nicht überschreiben, falls Repo-eigene config.json da ist) ===
if [ -n "$CONFIG_BAK" ] && [ -f "$CONFIG_BAK" ]; then
  # Wenn das neue Repo eine config.json mit neuen Keys enthält: Mergen!
  if [ -f config.json ]; then
    TMP_MERGE=$(mktemp)
    jq -s '.[0] * .[1]' config.json "$CONFIG_BAK" > "$TMP_MERGE"
    mv "$TMP_MERGE" config.json
    echo "Alte Konfiguration in neue config.json übernommen und gemerged."
  else
    cp "$CONFIG_BAK" config.json
    echo "Alte config.json wurde wiederhergestellt."
  fi
  sudo chown "$ADMIN_USER":"$ADMIN_USER" config.json
fi

# 5) Release-Branch/-Tag ermitteln
echo "Ermittle verfügbaren Release-Branch oder Tag..."

sudo -u "$ADMIN_USER" git fetch --all --tags

CONFIG_PATH="$INSTALL_DIR/config.json"
RELEASE_REF=""
if [ -f "$CONFIG_PATH" ]; then
  RELEASE_REF=$(jq -r '.release_branch // empty' "$CONFIG_PATH")
  RELEASE_REF=$(echo "$RELEASE_REF" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
fi

if [ -n "$RELEASE_REF" ]; then
  echo "-> Verwende release_branch aus config.json: '$RELEASE_REF'"
else
  BRANCHES_RAW=$(git branch -r | grep -E 'origin/release/v[0-9]+\.[0-9]+\.[0-9]+' | sed 's@origin/@@' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  if [ -n "$BRANCHES_RAW" ]; then
    RELEASE_REF=$(echo "$BRANCHES_RAW" | sort -V | tail -n1)
    echo "   Neuster Release-Branch gefunden: '$RELEASE_REF'"
  else
    TAGS_RAW=$(git tag -l --sort=-version:refname)
    if [ -n "$TAGS_RAW" ]; then
      RELEASE_REF=$(echo "$TAGS_RAW" | head -n1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
      echo "   Neustes Release-Tag gefunden: '$RELEASE_REF'"
    else
      echo "   Warnung: Keine Release-Branches oder Tags gefunden. Nutze 'main' als Fallback."
      RELEASE_REF="main"
    fi
  fi
  if [ -f "$CONFIG_PATH" ]; then
    tmpfile=$(mktemp)
    jq --arg ref "$RELEASE_REF" '.release_branch = $ref' "$CONFIG_PATH" > "$tmpfile"
    mv "$tmpfile" "$CONFIG_PATH"
    sudo chown "$ADMIN_USER":"$ADMIN_USER" "$CONFIG_PATH"
    echo "   config.json aktualisiert mit release_branch: '$RELEASE_REF'"
  else
    cat > "$CONFIG_PATH" <<EOF
{
  "release_branch": "$RELEASE_REF"
}
EOF
    sudo chown "$ADMIN_USER":"$ADMIN_USER" "$CONFIG_PATH"
    echo "   config.json neu erstellt mit release_branch: '$RELEASE_REF'"
  fi
fi

# 6) Checkout des Release-Refs (Branch oder Tag oder Remote-Branch)
if git show-ref --verify --quiet "refs/heads/$RELEASE_REF"; then
  echo "Checke lokalen Branch '$RELEASE_REF' aus"
  sudo -u "$ADMIN_USER" git checkout "$RELEASE_REF" -f
  sudo -u "$ADMIN_USER" git reset --hard "origin/$RELEASE_REF"
elif git show-ref --verify --quiet "refs/remotes/origin/$RELEASE_REF"; then
  echo "Checke Remote-Branch 'origin/$RELEASE_REF' aus und lege lokalen Branch an"
  sudo -u "$ADMIN_USER" git checkout -B "$RELEASE_REF" "origin/$RELEASE_REF"
elif git show-ref --verify --quiet "refs/tags/$RELEASE_REF"; then
  echo "Checke Tag '$RELEASE_REF' aus (detached HEAD)"
  sudo -u "$ADMIN_USER" git checkout "tags/$RELEASE_REF" -f
else
  echo "   Hinweis: '$RELEASE_REF' existiert weder als Branch noch als Tag. Verwende 'main'."
  RELEASE_REF="main"
  sudo -u "$ADMIN_USER" git checkout main -f
  sudo -u "$ADMIN_USER" git reset --hard origin/main
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

echo "=== Installation/Update abgeschlossen – Slideshow-App läuft unter $INSTALL_DIR, Release-Ref: $RELEASE_REF ==="
