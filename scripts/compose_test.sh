#!/usr/bin/env bash
# Validate and boot the supplied Compose setup with private paths and no hardware.
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
WORK=$(mktemp -d)
readonly WORK
readonly PROJECT="local-audio-compose-$$-$RANDOM"
CREATED=false
COMPOSE=(docker compose --project-name "$PROJECT" --project-directory "$REPOSITORY"
    --env-file "$WORK/compose.env" --file "$REPOSITORY/docker-compose.yml")

cleanup() {
    local result=$?
    if [ "$CREATED" = true ]; then
        if [ "$result" -ne 0 ]; then "${COMPOSE[@]}" logs >&2 || true; fi
        "${COMPOSE[@]}" down --volumes --remove-orphans --timeout 10 > /dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/pulse" "$WORK/sound"
touch "$WORK/cookie"
export PULSE_RUNTIME_DIR="$WORK/pulse" PULSE_COOKIE_FILE="$WORK/cookie"
export COMPOSE_TEST_ROOT="$WORK" COMPOSE_TEST_IMAGE="$IMAGE"
printf 'PULSE_RUNTIME_DIR=%s\nPULSE_COOKIE_FILE=%s\n' "$PULSE_RUNTIME_DIR" "$PULSE_COOKIE_FILE" > "$WORK/compose.env"
"${COMPOSE[@]}" config --quiet
printf '  ok   supplied Compose configuration validates with explicit audio paths\n'

cat > "$WORK/options.json" <<'OPTIONS'
{"zones":[{"id":"compose-first","name":"First room","output":"null"},{"id":"compose-second","name":"Second room","output":"stdout"}]}
OPTIONS
cat > "$WORK/override.yaml" <<'OVERRIDE'
services:
  local-audio-zones:
    image: ${COMPOSE_TEST_IMAGE:?}
    network_mode: none
    restart: "no"
    volumes:
      - type: bind
        source: ${COMPOSE_TEST_ROOT:?}/options.json
        target: /data/options.json
        read_only: true
      - type: bind
        source: ${COMPOSE_TEST_ROOT:?}/sound
        target: /dev/snd
        read_only: true
OVERRIDE
COMPOSE+=(--file "$WORK/override.yaml")
"${COMPOSE[@]}" config --format json > "$WORK/private.json"
jq -e --arg root "$WORK/" --arg image "$IMAGE" --arg project "$PROJECT" '
    .services["local-audio-zones"] as $service
    | $service.image == $image and $service.network_mode == "none"
    and ($service.privileged // false) == false and ($service.devices // [] | length) == 0
    and all($service.volumes[] | select(.type == "bind"); .source | startswith($root))
    and all(.volumes[]; (.external // false) == false and (.name | startswith($project + "_")))
' "$WORK/private.json" > /dev/null
[ -z "$(docker ps --all --quiet --filter "label=com.docker.compose.project=$PROJECT")" ]
[ -z "$(docker volume ls --quiet --filter "label=com.docker.compose.project=$PROJECT")" ]

wait_healthy() {
    local deadline=$((SECONDS + 45)) remaining
    while [ "$SECONDS" -lt "$deadline" ]; do
        remaining=$((deadline - SECONDS))
        if timeout "$remaining" "${COMPOSE[@]}" exec -T local-audio-zones /usr/bin/container-healthcheck > "$WORK/health.log" 2>&1; then
            return 0
        fi
        sleep 0.2
    done
    cat "$WORK/health.log" >&2
    return 1
}
check_players() {
    "${COMPOSE[@]}" exec -T local-audio-zones jq -e '
        map(.id) == ["compose-first","compose-second"]
        and map(.name) == ["First room","Second room"]
    ' /run/sendspin-cli/players.json > /dev/null
}

CREATED=true
"${COMPOSE[@]}" up --detach --pull never > /dev/null
wait_healthy
check_players
original=$("${COMPOSE[@]}" ps --quiet local-audio-zones)
"${COMPOSE[@]}" exec -T local-audio-zones bash -ec 'printf "retained\n" > /data/compose-fixture'
"${COMPOSE[@]}" up --detach --force-recreate --pull never > /dev/null
[ "$("${COMPOSE[@]}" ps --quiet local-audio-zones)" != "$original" ]
wait_healthy
check_players
[ "$("${COMPOSE[@]}" exec -T local-audio-zones cat /data/compose-fixture)" = retained ]
printf '  ok   Compose boots native players and preserves named-volume state after recreation\n'
