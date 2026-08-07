#!/bin/bash
# Runs daily database maintenance (trim old activity logs + old notifications).
#
# Not tied to a fixed hour on purpose — the server may not be running 24/7.
# This script is meant to run every hour via cron (like backup_db.sh), but
# only does actual work once per calendar day. It checks a marker file for
# the date it last ran; if that date is today, it exits immediately with no
# action. If not, it runs maintenance and updates the marker. This way,
# whenever the server happens to be up, maintenance catches up within an
# hour at most, with no dependency on a specific clock time.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER_FILE="$PROJECT_DIR/logs/.last_maintenance_date"
LOG_FILE="$PROJECT_DIR/logs/maintenance.log"
TODAY=$(date '+%Y-%m-%d')

mkdir -p "$PROJECT_DIR/logs"

# Already ran today? Nothing to do.
if [ -f "$MARKER_FILE" ] && [ "$(cat "$MARKER_FILE")" = "$TODAY" ]; then
    exit 0
fi

cd "$PROJECT_DIR"
{
    echo "$(date '+%Y-%m-%d %H:%M:%S') | == daily maintenance started =="
    docker compose exec -T web python manage.py trim_activity_logs
    docker compose exec -T web python manage.py trim_notifications
    echo "$(date '+%Y-%m-%d %H:%M:%S') | == daily maintenance finished =="
} >> "$LOG_FILE" 2>&1

echo "$TODAY" > "$MARKER_FILE"
