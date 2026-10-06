#!/usr/bin/env bash
# Exercise native USB relay supervision with no USB hardware or audio output.
# Usage: scripts/usb_trigger_service_test.sh <built-image>
set -euo pipefail

if [ "$#" -ne 1 ]; then
    printf 'usage: %s <built-image>\n' "$0" >&2
    exit 2
fi
readonly IMAGE=$1
readonly CONTAINER="${USB_TRIGGER_TEST_CONTAINER:-local-audio-usb-$$-$RANDOM}"
WORK=$(mktemp -d)
readonly WORK
CREATED=false

cleanup() {
    local status=$?
    if [ "$CREATED" = true ]; then
        if [ "$status" -ne 0 ]; then docker logs "$CONTAINER" >&2 || true; fi
        docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT

if docker inspect "$CONTAINER" > /dev/null 2>&1; then
    printf 'Container %s already exists; choose another test name.\n' "$CONTAINER" >&2
    exit 1
fi

cat > "$WORK/options.json" <<'OPTIONS'
{
  "zones": [
    {"id": "study", "name": "Study", "output": "null"},
    {"id": "bedroom", "name": "Bedroom", "output": "stdout"}
  ],
  "usb_relays": [
    {"name": "Amplifier", "device": "/dev/serial/by-id/usb-fixture-not-connected",
     "protocol": "DSD TECH SH-UR01A", "zones": ["study", "bedroom"], "off_delay": 0}
  ]
}
OPTIONS

docker run -d --name "$CONTAINER" --network none --no-healthcheck \
    --volume "$WORK/options.json:/data/options.json:ro" \
    "$IMAGE" > /dev/null
CREATED=true

wait_pid() {
    local service=$1 previous=${2:-} current='' attempt
    for ((attempt=0; attempt<150; attempt++)); do
        current=$(docker exec "$CONTAINER" s6-svstat -o pid \
            "/run/sendspin-cli/services/$service" 2> /dev/null || true)
        if [[ "$current" =~ ^[0-9]+$ ]] && [ "$current" -gt 0 ] && [ "$current" != "$previous" ]; then
            printf '%s' "$current"
            return 0
        fi
        [ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER")" = true ] || break
        sleep 0.1
    done
    printf 'Service %s did not start or recover.\n' "$service" >&2
    return 1
}

wait_health() {
    local attempt
    for ((attempt=0; attempt<100; attempt++)); do
        if docker exec "$CONTAINER" /usr/bin/container-healthcheck > "$WORK/health.log" 2>&1; then
            return 0
        fi
        sleep 0.1
    done
    cat "$WORK/health.log" >&2
    return 1
}

study_pid=$(wait_pid study)
bedroom_pid=$(wait_pid bedroom)
trigger_pid=$(wait_pid _usb-triggers)
wait_health
printf '  ok   two real players and the USB service start under native s6\n'

for ((attempt=0; attempt<100; attempt++)); do
    if docker logs "$CONTAINER" 2>&1 | grep -F 'USB relay /dev/serial/by-id/usb-fixture-not-connected unavailable' > /dev/null; then
        break
    fi
    sleep 0.1
done
docker logs "$CONTAINER" > "$WORK/logs" 2>&1
grep -F 'USB relay /dev/serial/by-id/usb-fixture-not-connected unavailable' "$WORK/logs" > /dev/null
docker exec "$CONTAINER" /usr/bin/container-healthcheck
printf '  ok   unavailable relay hardware is reported without disrupting audio health\n'

for signal in TERM KILL; do
    # Bash provides kill in the minimal runtime image.
    # shellcheck disable=SC2016
    docker exec "$CONTAINER" /bin/bash -euc 'kill -"$1" "$2"' bash "$signal" "$trigger_pid"
    trigger_pid=$(wait_pid _usb-triggers "$trigger_pid")
    [ "$(docker exec "$CONTAINER" s6-svstat -o pid /run/sendspin-cli/services/study)" = "$study_pid" ]
    [ "$(docker exec "$CONTAINER" s6-svstat -o pid /run/sendspin-cli/services/bedroom)" = "$bedroom_pid" ]
    wait_health
    printf '  ok   USB service recovers after %s without restarting players\n' "$signal"
done

docker stop --time 5 "$CONTAINER" > /dev/null
[ "$(docker inspect --format '{{.State.ExitCode}}' "$CONTAINER")" -eq 0 ]
[ "$(docker inspect --format '{{.State.Pid}}' "$CONTAINER")" -eq 0 ]
printf '  ok   native container shutdown completes within the stop deadline\n'
