#!/usr/bin/env bash
# Nattlig backup av openGym-dataene til Google Drive, kryptert med rclone.
# Settes opp etter docs/1-server-oracle.md, steg 9. Kjøres av cron som root.
set -euo pipefail

cd "$(dirname "$0")/.."
REMOTE="${BACKUP_REMOTE:-gdrive-krypt:}"   # rclone-remote som krypterer før opplasting
STAMP="$(date +%F)"
FILE="backups/opengym-$STAMP.tar.gz"

mkdir -p backups
tar czf "$FILE" data/
rclone copy "$FILE" "$REMOTE"

# Behold 14 dager lokalt og 60 dager i Google Drive.
find backups -name 'opengym-*.tar.gz' -mtime +14 -delete
rclone delete --min-age 60d "$REMOTE"

# Valgfritt: si fra til healthchecks.io at backupen gikk bra (får e-post hvis den uteblir).
if [ -n "${HEALTHCHECK_URL:-}" ]; then
  curl -fsS -m 10 --retry 3 "$HEALTHCHECK_URL" > /dev/null || true
fi

echo "Backup OK: $FILE"
