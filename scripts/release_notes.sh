#!/usr/bin/env bash
# Use the requested changelog section as the release body.
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ] || ! [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'usage: %s VERSION [CHANGELOG]\n' "$0" >&2
    exit 2
fi
version=$1
changelog=${2:-$(dirname -- "$0")/../local_audio_zones/CHANGELOG.md}

notes=$(awk -v version="$version" '
    { sub(/\r$/, "") }
    /^```/ { fence = !fence }
    !fence && /^## / {
        selected = ($0 == "## " version)
        if (selected) found++
        next
    }
    selected {
        body = body $0 "\n"
        if ($0 ~ /[^[:space:]]/) nonempty = 1
    }
    END {
        if (found != 1 || !nonempty) {
            print "Expected one nonempty changelog section for " version > "/dev/stderr"
            exit 1
        }
        printf "%s", body
    }
' "$changelog")
# Remove the separator before the changelog entry; retain its Markdown content.
notes=${notes#$'\n'}
printf '%s\n' "$notes"
