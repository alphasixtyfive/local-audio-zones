#!/usr/bin/env bash
# Check release ranges, Markdown rendering and invalid input.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
readonly SCRIPT_DIR
readonly NOTES="$SCRIPT_DIR/release_notes.sh"
readonly REAL_CHANGELOG="$SCRIPT_DIR/../local_audio_zones/CHANGELOG.md"
readonly IMAGE=ghcr.io/example/local-audio-zones

FAILURES=0
SCRATCH_ROOT=""

pass() {
    printf '  ok   %s\n' "$1"
}

fail() {
    printf '  FAIL %s\n' "$1" >&2
    FAILURES=$((FAILURES + 1))
}

step() {
    printf '\n%s\n' "$1"
}

assert_equal() {
    local want=$1 got=$2 what=$3
    if [ "$want" = "$got" ]; then
        pass "$what"
    else
        fail "$what"
        printf '    want: %q\n    got:  %q\n' "$want" "$got" >&2
    fi
}

body_of() {
    local out=''
    if ! out="$("$NOTES" --image "$IMAGE" "$@" 2>/dev/null)"; then
        printf 'error'
        return
    fi
    printf '%s' "$out"
}

status_of() {
    local status=0
    "$NOTES" --image "$IMAGE" "$@" >/dev/null 2>&1 || status=$?
    printf '%s' "$status"
}

assert_delivers() {
    local want=$1 what=$2
    shift 2
    local body got
    body="$(body_of "$@")"
    if [ "$body" = error ]; then
        fail "$what -- the script exited non-zero"
        return
    fi
    got="$(printf '%s\n' "$body" |
        sed -nE 's/^## ([0-9]+\.[0-9]+\.[0-9]+)[[:space:]]*$/\1/p' | tr '\n' ' ')"
    assert_equal "$want" "${got% }" "$what"
}

assert_refuses() {
    local want=$1 what=$2
    shift 2
    assert_equal "$want" "$(status_of "$@")" "$what"
}

check_the_shipped_changelog() {
    step 'the app changelog'
    assert_delivers '0.1.0' 'the first app release takes an empty --previous' \
        --version 0.1.0 --previous '' --changelog "$REAL_CHANGELOG"
    assert_delivers '0.1.0' 'a lower baseline selects the first app release' \
        --version 0.1.0 --previous 0.0.0 --changelog "$REAL_CHANGELOG"
}

check_the_lead_line() {
    step 'the release introduction'
    local body changelog="$SCRATCH_ROOT/range.md"
    body="$(body_of --version 0.1.0 --previous '' --changelog "$REAL_CHANGELOG")"
    assert_equal "Published as \`$IMAGE:0.1.0\` and \`$IMAGE:latest\`." \
        "$(printf '%s\n' "$body" | head -1)" \
        'the introduction names the image and both published tags'

    cat >"$changelog" <<'CHANGELOG'
# Changelog

## 1.4.0

Latest.

## 1.3.0

Third.

## 1.2.0

Second.

## 1.1.0

First.
CHANGELOG
    assert_delivers '1.4.0 1.3.0 1.2.0 1.1.0' 'an empty previous opens the whole range' \
        --version 1.4.0 --previous '' --changelog "$changelog"
    body="$(body_of --version 1.4.0 --previous 1.1.0 --changelog "$changelog")"
    assert_delivers '1.4.0 1.3.0 1.2.0' 'all sections since the previous release are included' \
        --version 1.4.0 --previous 1.1.0 --changelog "$changelog"
    assert_equal "Published as \`$IMAGE:1.4.0\` and \`$IMAGE:latest\`.

## 1.4.0" \
        "$(printf '%s\n' "$body" | head -3)" \
        'the introduction leads directly into the changelog'
    if printf '%s\n' "$body" | grep -q '^Also carries'; then
        fail 'the body contains development-history prose'
    else
        pass 'the body contains only the image introduction and changelog'
    fi
}

check_the_sections_are_verbatim() {
    step 'the sections are quoted rather than rewritten'

    local changelog="$SCRATCH_ROOT/verbatim.md"
    cat >"$changelog" <<'CHANGELOG'
# Changelog

## 1.2.0

Built on something, and through it something else.

- A bullet with `code`, **bold** and an em dash — in it.
  Wrapped onto a second line.

## 1.1.0

- The section above trails two blank lines; this one must still be one away.

## 1.0.0

Initial release.
CHANGELOG

    local want got
    # shellcheck disable=SC2016  # Markdown code spans.
    want='## 1.2.0

Built on something, and through it something else.

- A bullet with `code`, **bold** and an em dash — in it.
  Wrapped onto a second line.

## 1.1.0

- The section above trails two blank lines; this one must still be one away.'

    got="$(body_of --version 1.2.0 --previous 1.0.0 --changelog "$changelog" |
        sed -n '/^## /,$p')"
    assert_equal "$want" "$got" 'the sections come through verbatim, one blank line apart'
}

check_versions_are_compared_as_numbers() {
    step 'versions are numbers, not strings'

    local changelog="$SCRATCH_ROOT/numeric.md"
    cat >"$changelog" <<'CHANGELOG'
# Changelog

## 1.1.10

Ten.

## 1.1.9

Nine.

## 1.1.2

Two.
CHANGELOG

    assert_delivers '1.1.10' '1.1.10 is above 1.1.9, not below it' \
        --version 1.1.10 --previous 1.1.9 --changelog "$changelog"
    assert_delivers '1.1.10 1.1.9' 'a range spanning the ten and the nine carries both' \
        --version 1.1.10 --previous 1.1.2 --changelog "$changelog"
}

check_headings_bound_the_sections() {
    step 'headings that are not releases'

    local changelog="$SCRATCH_ROOT/headings.md"
    cat >"$changelog" <<'CHANGELOG'
# Changelog

## Unreleased

- Not written up under a version yet, and not part of any release.

## 1.1.0

### Fixed

- A subheading inside a section stays part of it.

## 1.0.0

Initial release.
CHANGELOG

    assert_delivers '1.1.0' 'an unversioned heading is not folded into the release below it' \
        --version 1.1.0 --previous 1.0.0 --changelog "$changelog"

    local body
    body="$(body_of --version 1.1.0 --previous 1.0.0 --changelog "$changelog")"
    if printf '%s\n' "$body" | grep -q 'Unreleased'; then
        fail 'the Unreleased section is left out of the body'
    else
        pass 'the Unreleased section is left out of the body'
    fi
    if printf '%s\n' "$body" | grep -q '^### Fixed$'; then
        pass 'a subheading inside a release section is kept'
    else
        fail 'a subheading inside a release section is kept'
    fi
}

check_fenced_blocks_are_content() {
    step 'fenced blocks are content, not structure'

    local changelog="$SCRATCH_ROOT/fenced.md"
    cat >"$changelog" <<'CHANGELOG'
# Changelog

## 2.0.0

Run this first:

```sh
# raise the output level
ha audio volume output 100
```

- The bullet after the block is still part of this section.

## 1.0.0

Initial release.
CHANGELOG

    assert_delivers '2.0.0' 'a fenced block does not close the section around it' \
        --version 2.0.0 --previous 1.0.0 --changelog "$changelog"

    local unclosed="$SCRATCH_ROOT/unclosed-fence.md"
    sed '/^```$/d' "$changelog" >"$unclosed"
    assert_refuses 1 'a code fence nobody closed is refused rather than run past' \
        --version 2.0.0 --previous 1.0.0 --changelog "$unclosed"

    local body line
    body="$(body_of --version 2.0.0 --previous 1.0.0 --changelog "$changelog")"
    for line in '# raise the output level' 'ha audio volume output 100' \
        '- The bullet after the block is still part of this section.'; do
        if printf '%s\n' "$body" | grep -qF -- "$line"; then
            pass "the body still carries '$line'"
        else
            fail "the body still carries '$line'"
        fi
    done
}

check_the_released_section_leads() {
    step 'the released version has to be the topmost section in range'

    local changelog="$SCRATCH_ROOT/misordered.md"
    cat >"$changelog" <<'CHANGELOG'
# Changelog

## 1.0.0

The older one, written above the newer.

## 2.0.0

Appended to the bottom rather than the top.
CHANGELOG

    assert_refuses 1 'a changelog written oldest-first is refused, not quietly reordered' \
        --version 2.0.0 --previous '' --changelog "$changelog"
}

check_it_refuses_rather_than_publishing_nothing() {
    step 'what it refuses to build a body out of'

    assert_refuses 1 'a version with no section is an error, not an empty body' \
        --version 9.9.9 --previous 0.1.0 --changelog "$REAL_CHANGELOG"

    assert_refuses 1 'a --previous at or above --version is refused' \
        --version 0.0.0 --previous 0.1.0 --changelog "$REAL_CHANGELOG"
    assert_refuses 1 'a --previous equal to --version is refused' \
        --version 0.1.0 --previous 0.1.0 --changelog "$REAL_CHANGELOG"

    assert_refuses 1 'a --version that is not MAJOR.MINOR.PATCH is refused' \
        --version v0.1.0 --previous 0.0.0 --changelog "$REAL_CHANGELOG"
    assert_refuses 1 'a --previous that is neither a version nor empty is refused' \
        --version 0.1.0 --previous v0.0.0 --changelog "$REAL_CHANGELOG"

    assert_refuses 1 'a changelog that does not exist is refused' \
        --version 0.1.0 --previous 0.0.0 --changelog "$SCRATCH_ROOT/nowhere.md"

    local status=0
    "$NOTES" --image "$IMAGE:0.1.0" --version 0.1.0 --previous 0.0.0 \
        --changelog "$REAL_CHANGELOG" >/dev/null 2>&1 || status=$?
    assert_equal 1 "$status" 'an --image that already carries a tag is refused'

    assert_refuses 2 'a missing --previous is a usage error, not an open range' \
        --version 0.1.0 --changelog "$REAL_CHANGELOG"
    assert_refuses 2 'a missing --version is a usage error' \
        --previous 0.0.0 --changelog "$REAL_CHANGELOG"
    assert_refuses 2 'a flag left without its value is a usage error' \
        --version 0.1.0 --previous
    assert_refuses 2 'an unknown flag is a usage error' \
        --version 0.1.0 --previous 0.0.0 --notes-file /dev/null
}

main() {
    printf 'release notes: checking %s\n' "$NOTES"

    [ -x "$NOTES" ] || {
        printf 'release notes: %s is not executable\n' "$NOTES" >&2
        exit 1
    }

    SCRATCH_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/release-notes-test.XXXXXX")"
    # shellcheck disable=SC2064  # Capture the scratch path now.
    trap "rm -rf -- '$SCRATCH_ROOT'" EXIT

    check_the_shipped_changelog
    check_the_lead_line
    check_the_sections_are_verbatim
    check_versions_are_compared_as_numbers
    check_headings_bound_the_sections
    check_fenced_blocks_are_content
    check_the_released_section_leads
    check_it_refuses_rather_than_publishing_nothing

    if [ "$FAILURES" -ne 0 ]; then
        printf '\nrelease notes: %d check(s) failed\n' "$FAILURES" >&2
        exit 1
    fi
    printf '\nrelease notes: every check passed\n'
}

main
