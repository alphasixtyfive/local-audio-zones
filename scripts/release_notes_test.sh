#!/usr/bin/env bash
# Check version selection and malformed changelog entries.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
readonly SCRIPT_DIR
readonly NOTES="$SCRIPT_DIR/release_notes.sh"
WORK=$(mktemp -d)
readonly WORK
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/changelog.md" <<'CHANGELOG'
# Changelog

## 1.2.0

Latest release.

```text
## 9.9.9
```

## 1.1.0

Previous release.
CHANGELOG
expected='Latest release.

```text
## 9.9.9
```'
[ "$(bash "$NOTES" 1.2.0 "$WORK/changelog.md")" = "$expected" ]
[ "$(bash "$NOTES" 1.1.0 "$WORK/changelog.md")" = 'Previous release.' ]

reject() {
    if bash "$NOTES" "$@" > /dev/null 2>&1; then
        printf 'Unexpectedly accepted: %s\n' "$*" >&2
        exit 1
    fi
}
reject 9.0.0 "$WORK/changelog.md"
reject v1.2.0 "$WORK/changelog.md"
reject 1.2.0 "$WORK/missing.md"
reject
printf '## 1.2.0\n' > "$WORK/empty.md"
reject 1.2.0 "$WORK/empty.md"
printf '## 1.2.0\nOne.\n## 1.2.0\nTwo.\n' > "$WORK/duplicate.md"
reject 1.2.0 "$WORK/duplicate.md"

bash "$NOTES" 0.1.0 > "$WORK/shipped.md"
grep -Fx 'First release.' "$WORK/shipped.md" > /dev/null
printf 'Changelog selection checks passed.\n'
