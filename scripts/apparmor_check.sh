#!/usr/bin/env bash
# Parse the profile under both installed slug shapes used by Supervisor.
set -euo pipefail

if [ "$#" -gt 1 ]; then
    printf 'usage: %s [PROFILE]\n' "$0" >&2
    exit 2
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
readonly SCRIPT_DIR
PROFILE=${1:-$SCRIPT_DIR/../local_audio_zones/apparmor.txt}
readonly PROFILE

command -v apparmor_parser > /dev/null || { printf 'apparmor_parser is required\n' >&2; exit 1; }
[ -f "$PROFILE" ] || { printf 'Profile not found: %s\n' "$PROFILE" >&2; exit 1; }
names=$(awk '/^profile [^ ]+/ {print $2}' "$PROFILE")
[ "$(printf '%s\n' "$names" | grep -c . || true)" -eq 1 ] || {
    printf 'Expected exactly one top-level AppArmor profile\n' >&2
    exit 1
}

WORK=$(mktemp -d)
readonly WORK
trap 'rm -rf -- "$WORK"' EXIT
for slug in local_local_audio_zones a0d7b954_local_audio_zones; do
    awk -v slug="$slug" '{if (/^profile /) $2 = slug; print}' "$PROFILE" > "$WORK/$slug"
    apparmor_parser --skip-kernel-load --skip-cache "$WORK/$slug"
    printf 'AppArmor parses as %s\n' "$slug"
done
