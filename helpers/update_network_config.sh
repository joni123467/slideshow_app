#!/bin/bash
# update_network_config.sh
#
# Aufruf:
#   update_network_config.sh <MODE> <STATIC_IP> <ROUTERS> <DNS>
#
#   MODE        = "dhcp" oder "static"
#   STATIC_IP   = "192.168.66.129/24" (inkl. /24) oder leer
#   ROUTERS     = "192.168.66.1"      oder leer
#   DNS         = "8.8.8.8"           oder leer
#
# Voraussetzungen:
#   - NetworkManager ist aktiv (dhcpcd ist deaktiviert).
#   - Schnittstelle heißt "eth0". Bei abweichender Bezeichnung "INTERFACE" anpassen.
#   - Script ist ausführbar (chmod +x).
#   - In /etc/sudoers.d/... oder /etc/sudoers konfiguriert, damit "sudo /usr/local/bin/update_network_config.sh" ohne Passwort läuft.

MODE="$1"
STATIC_IP="$2"
ROUTERS="$3"
DNS="$4"

# Name und Interface anpassen falls gewünscht
CON_NAME="MyEthernet"
INTERFACE="eth0"

# Prüfen, ob nmcli vorhanden ist
if ! command -v nmcli >/dev/null 2>&1; then
  echo "Fehler: nmcli nicht gefunden. NetworkManager ist evtl. nicht installiert?" >&2
  exit 1
fi

# Vorherige Verbindung löschen, falls existiert
EXISTS=$(nmcli -g NAME connection show | grep -Fx "$CON_NAME")
if [ -n "$EXISTS" ]; then
  nmcli connection delete "$CON_NAME"
fi

# Falls "dhcp" => DHCP-Verbindung anlegen
if [ "$MODE" = "dhcp" ]; then
  nmcli connection add type ethernet ifname "$INTERFACE" con-name "$CON_NAME" \
      ipv4.method auto
  echo "DHCP-Konfiguration für $INTERFACE erzeugt."
else
  # "static" => Statische IP anlegen
  # Achte darauf, STATIC_IP muss Form "192.168.66.129/24" haben
  nmcli connection add type ethernet ifname "$INTERFACE" con-name "$CON_NAME" \
      ipv4.addresses "$STATIC_IP" \
      ipv4.gateway "$ROUTERS" \
      ipv4.dns "$DNS" \
      ipv4.method manual
  echo "Statische Konfiguration für $INTERFACE erzeugt: $STATIC_IP gw:$ROUTERS dns:$DNS"
fi

# Schnittstelle hochfahren
nmcli connection up "$CON_NAME"

exit 0

