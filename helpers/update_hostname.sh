#!/bin/bash
# update_hostname.sh - erwartet den neuen Hostnamen als Parameter
NEW_HOSTNAME="$1"
if [ -z "$NEW_HOSTNAME" ]; then
  echo "Kein Hostname angegeben." >&2
  exit 1
fi

echo "$NEW_HOSTNAME" > /etc/hostname
# Ersetze in /etc/hosts die Zeile, die mit 127.0.1.1 beginnt
sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts
exit 0

