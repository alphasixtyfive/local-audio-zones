#!/usr/bin/env bash
# Render changelog entries since the previous release.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
readonly SCRIPT_DIR

VERSION=''
PREVIOUS=''
IMAGE=''
CHANGELOG="$SCRIPT_DIR/../local_audio_zones/CHANGELOG.md"
have_previous=0

usage() {
    printf 'usage: %s --version X.Y.Z --previous [X.Y.Z|%s] --image REPOSITORY [--changelog PATH]\n' \
        "$0" "''" >&2
}

die() {
    printf '::error::release notes: %s\n' "$1" >&2
    exit 1
}

need_value() {
    [ "$1" -ge 2 ] || {
        usage
        exit 2
    }
}

while [ "$#" -gt 0 ]; do
    case $1 in
        --version)
            need_value "$#"
            VERSION=$2
            shift 2
            ;;
        --previous)
            need_value "$#"
            PREVIOUS=$2
            have_previous=1
            shift 2
            ;;
        --image)
            need_value "$#"
            IMAGE=$2
            shift 2
            ;;
        --changelog)
            need_value "$#"
            CHANGELOG=$2
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

[ -n "$VERSION" ] || {
    usage
    exit 2
}
[ -n "$IMAGE" ] || {
    usage
    exit 2
}
[ "$have_previous" = 1 ] || {
    usage
    exit 2
}

readonly VERSION PREVIOUS IMAGE CHANGELOG

readonly SEMVER='^[0-9]+\.[0-9]+\.[0-9]+$'
[[ "$VERSION" =~ $SEMVER ]] || die "--version $VERSION is not MAJOR.MINOR.PATCH"
[ -z "$PREVIOUS" ] || [[ "$PREVIOUS" =~ $SEMVER ]] ||
    die "--previous $PREVIOUS is neither MAJOR.MINOR.PATCH nor empty"
[[ "$IMAGE" =~ ^[a-z0-9._/-]+$ ]] ||
    die "--image must be an untagged repository"

[ -f "$CHANGELOG" ] || die "changelog not found: $CHANGELOG"

version_lt() {
    awk -v a="$1" -v b="$2" '
        BEGIN {
            split(a, x, "."); split(b, y, ".")
            for (i = 1; i <= 3; i++)
                if (x[i] + 0 != y[i] + 0) exit !(x[i] + 0 < y[i] + 0)
            exit 1
        }'
}

[ -z "$PREVIOUS" ] || version_lt "$PREVIOUS" "$VERSION" ||
    die "--previous must be lower than --version"

render_sections() {
    awk -v version="$VERSION" -v previous="$PREVIOUS" '
        function cmp(a, b,   x, y, i) {
            split(a, x, "."); split(b, y, ".")
            for (i = 1; i <= 3; i++) {
                if (x[i] + 0 < y[i] + 0) return -1
                if (x[i] + 0 > y[i] + 0) return 1
            }
            return 0
        }

        function emit() {
            while (pending > 0) { print ""; pending-- }
            print
            printed = 1
        }

        { sub(/\r$/, "") }

        # Headings inside code fences are content.
        /^```/ { fence = !fence }

        !fence && /^## [0-9]+\.[0-9]+\.[0-9]+[[:space:]]*$/ {
            keep = (previous == "" || cmp($2, previous) > 0) && cmp($2, version) <= 0
            if (keep) {
                if (printed) pending = 1
                emit()
            }
            next
        }

        !fence && /^##?([^#]|$)/ { keep = 0; next }

        keep {
            # Drop trailing blank lines between sections.
            if ($0 ~ /^[[:space:]]*$/) {
                if (printed) pending++
                next
            }
            emit()
        }

        END {
            if (fence)
                print "::error::release notes: unclosed code fence in " FILENAME > "/dev/stderr"
            exit fence ? 1 : 0
        }' "$CHANGELOG"
}

sections="$(render_sections)"

mapfile -t delivered < <(printf '%s\n' "$sections" | sed -nE 's/^## ([0-9]+\.[0-9]+\.[0-9]+)[[:space:]]*$/\1/p')

printf '%s\n' "${delivered[@]}" | grep -qxF "$VERSION" ||
    die "changelog has no '## $VERSION' section"

[ "${delivered[0]}" = "$VERSION" ] ||
    die "released version must be the first selected changelog section"

# shellcheck disable=SC2016  # Markdown code spans.
printf 'Published as `%s:%s` and `%s:latest`.\n' "$IMAGE" "$VERSION" "$IMAGE"

printf '\n%s\n' "$sections"
