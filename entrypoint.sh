#!/bin/bash
# Do NOT use set -e — chown/chmod may fail on restricted mounts and must not kill the container
PUID=${PUID:-99}
PGID=${PGID:-100}
echo "Starting with UID=$PUID, GID=$PGID"

# Adjust scraper user/group to match host UID/GID
groupmod -o -g "$PGID" scraper 2>/dev/null || true
usermod -o -u "$PUID" -g "$PGID" scraper 2>/dev/null || true

# Fix /app/data mount point first
chown "$PUID:$PGID" /app/data 2>/dev/null || chmod 777 /app/data 2>/dev/null || true

# Create and fix each subdirectory individually
# On Unraid, recursive chmod on a mount point may not actually recurse,
# so we must handle each directory explicitly
for dir in logs config temp wallpapers thumbnails; do
    target="/app/data/$dir"
    mkdir -p "$target" 2>/dev/null || true
    # Try chown first, then chmod, for each directory individually
    chown -R "$PUID:$PGID" "$target" 2>/dev/null || \
    chmod -R 777 "$target" 2>/dev/null || true
done

# Verify the scraper user can actually write — if not, print clear diagnostics
if gosu scraper touch /app/data/config/.write_test 2>/dev/null; then
    rm -f /app/data/config/.write_test
    echo "Permissions OK — scraper user can write to /app/data"
else
    echo "WARN: scraper user cannot write to /app/data — trying final chmod on each dir"
    # Last resort: chmod each dir as root, one at a time
    for dir in logs config temp wallpapers thumbnails; do
        chmod 777 "/app/data/$dir" 2>/dev/null || true
        # Also chmod the mount point entries themselves
        chmod a+rwx "/app/data/$dir" 2>/dev/null || true
    done
    chmod 777 /app/data 2>/dev/null || true
    # Verify again
    if gosu scraper touch /app/data/config/.write_test 2>/dev/null; then
        rm -f /app/data/config/.write_test
        echo "Permissions fixed after retry"
    else
        echo "WARN: Permission fix failed — app will use fallback paths (/tmp/scraper-data)"
    fi
fi

# Ensure fallback directory is ready in case the app needs it
mkdir -p /tmp/scraper-data/{logs,config,temp,wallpapers,thumbnails} 2>/dev/null || true
chown -R "$PUID:$PGID" /tmp/scraper-data 2>/dev/null || true

chown -R "$PUID:$PGID" /app/src 2>/dev/null || true
chown -R "$PUID:$PGID" /opt/playwright-browsers 2>/dev/null || true

# Copy model cache to scraper user home if needed
if [ -d /root/.cache/huggingface ] && [ ! -d /home/scraper/.cache/huggingface ]; then
    mkdir -p /home/scraper/.cache
    cp -r /root/.cache/huggingface /home/scraper/.cache/huggingface 2>/dev/null || true
    chown -R "$PUID:$PGID" /home/scraper/.cache 2>/dev/null || true
    echo "Model cache copied to scraper user"
fi

echo "Permissions fixed. Dropping to user scraper (UID=$PUID, GID=$PGID)..."
exec gosu scraper "$@"
