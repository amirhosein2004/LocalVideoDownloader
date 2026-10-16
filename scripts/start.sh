#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
command -v docker >/dev/null 2>&1 || { echo 'Install Docker with the Compose plugin first.' >&2; exit 1; }
download_dir=''
if [ "${1:-}" = '--download-dir' ]; then
    [ "$#" -eq 2 ] || { echo 'Usage: sh scripts/start.sh [--download-dir /full/path]' >&2; exit 1; }
    download_dir=$2
elif [ "$#" -gt 0 ]; then
    echo 'Usage: sh scripts/start.sh [--download-dir /full/path]' >&2; exit 1
fi
if [ ! -f .env ] || [ -n "$download_dir" ]; then
    if [ -z "$download_dir" ]; then
        if command -v xdg-user-dir >/dev/null 2>&1; then download_dir=$(xdg-user-dir DOWNLOAD); fi
        download_dir=${download_dir:-"$HOME/Downloads"}
    fi
    case "$download_dir" in /*) ;; *) echo 'Use a full absolute folder path.' >&2; exit 1;; esac
    case "$download_dir" in *"'"*) echo 'Single quotes in folder names are not supported.' >&2; exit 1;; esac
    # Reject multiline dotenv values.
    [ "$(printf '%s' "$download_dir" | wc -l)" -eq 0 ] || exit 1
    mkdir -p -- "$download_dir"
    download_dir=$(CDPATH= cd -- "$download_dir" && pwd)
    port=${APP_PORT:-8765}
    printf "DOWNLOAD_DIR='%s'\nAPP_PORT=%s\n" "$download_dir" "$port" > .env
fi
docker compose up --build -d
port=$(sed -n 's/^APP_PORT=\([0-9][0-9]*\)$/\1/p' .env | head -n 1)
url="http://127.0.0.1:${port:-8765}"
echo "Open $url"
if command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 &
elif command -v open >/dev/null 2>&1; then open "$url"; fi
