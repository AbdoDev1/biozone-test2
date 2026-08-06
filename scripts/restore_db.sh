#!/bin/bash
# Restore a Biozone database backup.
#
# Takes a .sql.gz file produced by backup_db.sh, wipes the current
# public schema completely, and restores the data from the backup
# instead — a "clean" restore with no conflicts against existing data.
#
# WARNING: this is destructive. Any data currently in the database
# will be permanently deleted before the restore. No undo after
# confirmation.
#
# Usage (run from the project directory, next to docker-compose.yml):
#   ./scripts/restore_db.sh backups/biozone_2026-08-05_120000.sql.gz

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env.production"
LOG_FILE="$PROJECT_DIR/logs/restore.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') | $1" | tee -a "$LOG_FILE"
}

if [ $# -ne 1 ]; then
    echo "Usage: $0 path-to-backup.sql.gz"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Error: file $BACKUP_FILE not found."
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: $ENV_FILE not found. Run this script from the project directory."
    exit 1
fi

DB_NAME=$(grep -E '^DB_NAME=' "$ENV_FILE" | cut -d '=' -f2-)
DB_USER=$(grep -E '^DB_USER=' "$ENV_FILE" | cut -d '=' -f2-)
DB_PASSWORD=$(grep -E '^DB_PASSWORD=' "$ENV_FILE" | cut -d '=' -f2-)

if [ -z "$DB_NAME" ] || [ -z "$DB_USER" ]; then
    echo "Error: DB_NAME or DB_USER missing from $ENV_FILE."
    exit 1
fi

echo "WARNING: this will permanently delete all current data in database '$DB_NAME'"
echo "   and restore it from: $BACKUP_FILE"
echo ""
read -p "Type YES (all caps) to confirm you want to proceed: " CONFIRM
if [ "$CONFIRM" != "YES" ]; then
    echo "Cancelled. No changes made."
    exit 1
fi

mkdir -p "$PROJECT_DIR/logs"
cd "$PROJECT_DIR"

log "== Starting restore from: $BACKUP_FILE =="

log "Stopping web container (prevents writes during restore)..."
docker compose stop web

log "Dropping and recreating the public schema..."
docker compose exec -T -e PGPASSWORD="$DB_PASSWORD" db \
    psql -U "$DB_USER" "$DB_NAME" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

log "Restoring data from backup..."
if gunzip -c "$BACKUP_FILE" | docker compose exec -T -e PGPASSWORD="$DB_PASSWORD" db \
        psql -U "$DB_USER" "$DB_NAME"; then
    log "Restore completed successfully."
else
    log "Restore failed! Check the messages above."
    docker compose start web
    exit 1
fi

log "Restarting web container..."
docker compose start web

log "== Done =="
echo "Restore complete. Open the site and verify the data."
