#!/usr/bin/env bash
# Exercise the same native runtime checks before building or releasing an image.
set -euo pipefail

if [ "$#" -ne 1 ]; then
    printf 'usage: %s <built-image>\n' "$0" >&2
    exit 2
fi
readonly IMAGE=$1
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
readonly SCRIPT_DIR
REPOSITORY="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly REPOSITORY
readonly AUDIO_IMAGE="local-audio-audio-test:$$"
trap 'docker image rm "$AUDIO_IMAGE" > /dev/null 2>&1 || true' EXIT

"$SCRIPT_DIR/smoke_test.sh" "$IMAGE" --require-apparmor
"$SCRIPT_DIR/multi_zone_supervision_test.sh" "$IMAGE"

docker run --rm --network none --entrypoint python3 \
    --env PYTHONDONTWRITEBYTECODE=1 --volume "$REPOSITORY:/repo:ro" \
    "$IMAGE" /repo/scripts/audio_routing_test.py

docker build --build-arg "PLAYER_IMAGE=$IMAGE" \
    --file "$SCRIPT_DIR/Dockerfile.audio-test" --tag "$AUDIO_IMAGE" "$SCRIPT_DIR"
docker run --rm --network none --volume "$REPOSITORY:/repo:ro" \
    "$AUDIO_IMAGE" /repo/scripts/pulse_isolation_test.py
