Slideshow App

Übersicht

Die Slideshow App besteht aus zwei Komponenten:

slideshow.py: Ein Python-/Pygame-Skript, das Bilder aus lokalen Verzeichnissen oder SMB-Freigaben im Vollbild- oder Split-Screen-Modus anzeigt.

app.py: Ein Flask-Webinterface zur Konfiguration der Slideshow, Anzeige von Logs und zum Auslösen von Updates.

Zusätzlich gibt es Helper-Skripte und systemd-Service-Units für automatischen Start und Updates.

Funktionen

Automatischer Start über systemd

Web-UI (Flask) für:

Konfiguration (Einstellungen, SMB-Zugangsdaten, Modi)

Log-Level-Änderung

Anzeige der letzten Log-Einträge

Auslösen von Updates

Update-Mechanismus mit update.sh (Git-Pull, Virtualenv, Service-Restart)

Helper-Skripte für Hostname- und Netzwerk-Konfiguration

Voraussetzungen

Betriebssystem: Debian-/Ubuntu-basierte Distribution (z. B. Raspberry Pi OS)

Python 3.8 oder neuer

System-Pakete:

```
sudo apt update
sudo apt install -y \
  git python3 python3-venv python3-pip python3-dev libpam0g-dev libsmbclient-dev build-essential
```

Installation

Installationsskript herunterladen und ausführen

```
cd ~
git clone git@github.com:joni123467/slideshow_app.git
cd slideshow_app
sudo ./install.sh
```

Das Skript:

Legt den System-Benutzer administrator an (mit Home und sudo-Rechten)

Klont oder aktualisiert das Repo unter /home/administrator/slideshow_app

Erstellt ein Virtualenv und installiert Python-Abhängigkeiten

Deployt Helper-Skripte und systemd-Units

Legt Sudoers-Einträge für automatische Updates an

Aktiviert und startet die Services

Update

Per Web-UI: Im Menü auf Update ausführen klicken. Die Seite zeigt einen Lade-Spinner und leitet nach definierten Sekunden zurück.

Manuell via SSH:

```
cd /home/administrator/slideshow_app
sudo ./update.sh
```

Das update.sh führt Git-Pull, Dependency-Update und Service-Restarts durch.

Dienste verwalten (systemd)

Slideshow-Service:

```
sudo systemctl status slideshow.service
sudo systemctl restart slideshow.service
```

Flask-App-Service:

```
sudo systemctl status app.service
sudo systemctl restart app.service
```

Beide Services werden beim Boot automatisch gestartet:

```
sudo systemctl enable slideshow.service app.service
```

Konfigurationsdatei

Alle Einstellungen stehen in config.json im App-Verzeichnis:

```
{
  "mode": "slideshow",
  "split_screen": false,
  "image_path": "smb://```",
  "display_duration": 5,
  "rotation": 0,
  "smb_username": "TV",
  "smb_domain": "MYDOMAIN",
  "smb_password": "*****",
  "stretch_images": true,
  "log_level": "DEBUG"
}
```

Änderungen werden von der Slideshow automatisch alle Sekunde neu eingelesen.

Verzeichnisstruktur

```
slideshow_app/
├── app.py                   # Flask-Webapp
├── slideshow.py             # Pygame-Slideshow
├── install.sh               # Erstinstallation
├── update.sh                # Update-Pipeline
├── requirements.txt         # Python-Abhängigkeiten
├── config.json              # Beispiel-Konfiguration
├── update.log               # Update-Logfile
├── systemd/                 # systemd-Unit-Dateien
│   ├── slideshow.service
│   └── app.service
├── helpers/                 # Helper-Skripte
│   ├── update_hostname.sh
│   └── update_network_config.sh
└── templates/               # Flask-Templates
    ├── index.html
    ├── config.html
    └── updating.html
```

