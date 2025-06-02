#!/usr/bin/env bash
set -eux

# =============================================================================
# Slideshow-App Update-Skript
# Wenn in config.json ein "release_branch" definiert ist, wird dieser
# Branch verwendet. Nur wenn kein Branch in config.json steht und keine Tags
# existieren, fällt das Skript auf "main" zurück.
# =============================================================================

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# --- 1) Sicherstellen, dass 'jq' installiert ist ---
if ! command -v jq &>/dev/null; then
  echo "Fehler: 'jq' fehlt. Installiere jq..."
  sudo apt update
  sudo apt install -y jq
fi

# --- 2) Sudoers-Einträge prüfen/erstellen ---
SUDOERS_FILE=/etc/sudoers.d/slideshow_app
if ! sudo test -f "$SUDOERS_FILE"; then
  echo "-> Erstelle Sudoers-Eintrag für automatisches Update"
  sudo tee "$SUDOERS_FILE" > /dev/null <<'EOF'
# Erlaubt Kopieren der systemd-Units
administrator ALL=(ALL) NOPASSWD: \
  /bin/cp /home/administrator/slideshow_app/systemd/slideshow.service /etc/systemd/system/slideshow.service, \
  /bin/cp /home/administrator/slideshow_app/systemd/app.service /etc/systemd/system/app.service

# Erlaubt Kopieren der Helper-Skripte
administrator ALL=(ALL) NOPASSWD: \
  /bin/cp /home/administrator/slideshow_app/helpers/update_hostname.sh /usr/local/bin/update_hostname.sh, \
  /bin/cp /home/administrator/slideshow_app/helpers/update_network_config.sh /usr/local/bin/update_network_config.sh

# Erlaubt systemd-Reload und Restart/Start der Dienste
administrator ALL=(ALL) NOPASSWD: \
  /usr/bin/systemctl daemon-reload, \
  /usr/bin/systemctl restart slideshow.service, \
  /usr/bin/systemctl start slideshow.service, \
  /usr/bin/systemctl restart app.service, \
  /usr/bin/systemctl start app.service

# Erlaubt chmod +x der Helper-Skripte
administrator ALL=(ALL) NOPASSWD: \
  /bin/chmod +x /usr/local/bin/update_hostname.sh, \
  /bin/chmod +x /usr/local/bin/update_network_config.sh
EOF
  sudo chmod 440 "$SUDOERS_FILE"
else
  echo "-> Sudoers-Eintrag bereits vorhanden"
fi

# --- 3) Logging vorbereiten ---
LOGFILE="$BASE_DIR/update.log"
echo "" >> "$LOGFILE"
echo "=== Update startet: $(date '+%Y-%m-%d %H:%M:%S') ===" | tee -a "$LOGFILE"

# --- 4) Release-Branch/-Tag aus config.json lesen ---
CONFIG_PATH="$BASE_DIR/config.json"
RELEASE_REF=""

if [ -f "$CONFIG_PATH" ]; then
  # Lese den Wert, falls vorhanden. Falls key fehlt oder leer, bleibt RELEASE_REF leer.
  RELEASE_REF=$(jq -r '.release_branch // empty' "$CONFIG_PATH")
fi

if [ -n "$RELEASE_REF" ]; then
  echo "-> Verwende Release-Branch/-Tag aus config.json: $RELEASE_REF" | tee -a "$LOGFILE"
else
  echo "-> Kein release_branch in config.json definiert, ermittle neuesten Tag…" | tee -a "$LOGFILE"
  # Stelle sicher, dass alle Tags lokal vorliegen
  git fetch --all --tags | tee -a "$LOGFILE"
  # Ermittle das neueste Tag (semantisch sortiert)
  LATEST_TAG=$(git tag -l --sort=-version:refname | head -n1)
  if [ -n "$LATEST_TAG" ]; then
    RELEASE_REF="$LATEST_TAG"
    echo "   Neuestes Tag gefunden: $RELEASE_REF" | tee -a "$LOGFILE"
  else
    echo "   Warnung: Keine Tags gefunden, verwende 'main' als Fallback" | tee -a "$LOGFILE"
    RELEASE_REF="main"
  fi

  # Schreibe das ermittelte Release-Ref zurück in config.json
  if [ -f "$CONFIG_PATH" ]; then
    tmpfile=$(mktemp)
    jq --arg ref "$RELEASE_REF" '.release_branch = $ref' "$CONFIG_PATH" > "$tmpfile"
    mv "$tmpfile" "$CONFIG_PATH"
    echo "   config.json aktualisiert mit release_branch: $RELEASE_REF" | tee -a "$LOGFILE"
  else
    cat > "$CONFIG_PATH" <<EOF
{
  "release_branch": "$RELEASE_REF"
}
EOF
    echo "   config.json angelegt mit release_branch: $RELEASE_REF" | tee -a "$LOGFILE"
  fi
fi

# --- 5) Git fetch -- alle Branches und Tags, dann Checkout des Release-Refs ---
echo "-> Git fetch --all" | tee -a "$LOGFILE"
git fetch --all | tee -a "$LOGFILE"

# Prüfe, ob der Release-Ref ein Branch ist
if git show-ref --verify --quiet "refs/heads/$RELEASE_REF"; then
  echo "-> Checke Branch '$RELEASE_REF' aus" | tee -a "$LOGFILE"
  git checkout "$RELEASE_REF" -f | tee -a "$LOGFILE"
  echo "-> Reset auf origin/$RELEASE_REF" | tee -a "$LOGFILE"
  git reset --hard "origin/$RELEASE_REF" | tee -a "$LOGFILE"

# Prüfe, ob der Release-Ref ein Tag ist
elif git show-ref --verify --quiet "refs/tags/$RELEASE_REF"; then
  echo "-> Checke Tag '$RELEASE_REF' aus (detached HEAD)" | tee -a "$LOGFILE"
  git checkout "tags/$RELEASE_REF" -f | tee -a "$LOGFILE"

# Wenn weder Branch noch Tag existieren (unwahrscheinlich), falle auf main zurück
else
  echo "   Hinweis: '$RELEASE_REF' existiert nicht als Branch oder Tag. Nutze 'main'." | tee -a "$LOGFILE"
  RELEASE_REF="main"
  git checkout main -f | tee -a "$LOGFILE"
  git reset --hard origin/main | tee -a "$LOGFILE"
fi

# --- 6) Virtualenv prüfen/erstellen ---
echo "-> Virtualenv prüfen/erstellen" | tee -a "$LOGFILE"
if [ ! -d venv ]; then
  python3 -m venv venv | tee -a "$LOGFILE"
  echo "   Virtualenv angelegt" | tee -a "$LOGFILE"
else
  echo "   Virtualenv existiert" | tee -a "$LOGFILE"
fi

VENV_PIP="$BASE_DIR/venv/bin/pip"

# --- 7) Dependencies installieren ---
echo "-> Installiere Dependencies" | tee -a "$LOGFILE"
if [ -f requirements.txt ]; then
  $VENV_PIP install --upgrade pip | tee -a "$LOGFILE"
  $VENV_PIP install -r requirements.txt | tee -a "$LOGFILE"
else
  echo "   Keine requirements.txt gefunden, überspringe" | tee -a "$LOGFILE"
fi

# --- 8) Service-Unit-Dateien deployen ---
echo "-> Deploy Service-Units" | tee -a "$LOGFILE"
for svcfile in slideshow.service app.service; do
  if [ -f "$BASE_DIR/systemd/$svcfile" ]; then
    echo "   Kopiere $svcfile" | tee -a "$LOGFILE"
    sudo cp "$BASE_DIR/systemd/$svcfile" /etc/systemd/system/ | tee -a "$LOGFILE"
  fi
done

# --- 9) Helper-Skripte deployen ---
echo "-> Deploy Helper-Skripte" | tee -a "$LOGFILE"
for helper in helpers/update_hostname.sh helpers/update_network_config.sh; do
  if [ -f "$BASE_DIR/$helper" ]; then
    echo "   Kopiere $helper" | tee -a "$LOGFILE"
    sudo cp "$BASE_DIR/$helper" /usr/local/bin/ | tee -a "$LOGFILE"
    sudo chmod +x "/usr/local/bin/$(basename "$helper")" | tee -a "$LOGFILE"
  fi
done

# --- 10) systemd neu laden & Dienste neu starten ---
echo "-> Reload systemd daemon" | tee -a "$LOGFILE"
sudo systemctl daemon-reload | tee -a "$LOGFILE"

echo "-> Restart Services" | tee -a "$LOGFILE"
for svc in slideshow app; do
  unit="${svc}.service"
  if systemctl is-active --quiet "$unit"; then
    echo "   Restart $unit" | tee -a "$LOGFILE"
    sudo systemctl restart "$unit" | tee -a "$LOGFILE"
  else
    echo "   Start $unit" | tee -a "$LOGFILE"
    sudo systemctl start "$unit" | tee -a "$LOGFILE"
  fi
done

echo "=== Update beendet: $(date '+%Y-%m-%d %H:%M:%S') – Release-Ref: $RELEASE_REF ===" | tee -a "$LOGFILE"

