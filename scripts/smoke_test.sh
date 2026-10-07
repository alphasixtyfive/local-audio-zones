#!/usr/bin/env bash
# Boot the native Home Assistant app with private options, ports and state.
# Usage: scripts/smoke_test.sh <built-image> [--require-apparmor]
set -euo pipefail
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    printf 'usage: %s <image> [--require-apparmor]\n' "$0" >&2
    exit 2
fi
readonly IMAGE=$1
REQUIRE_APPARMOR=false
if [ "$#" -eq 2 ]; then
    [ "$2" = --require-apparmor ] || exit 2
    REQUIRE_APPARMOR=true
fi
readonly REQUIRE_APPARMOR
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
readonly SCRIPT_DIR
WORK=$(mktemp -d)
readonly WORK
readonly PREFIX="local-audio-native-$$"
readonly PROFILE="${PREFIX}-profile"
CONTAINERS=()
PLAYER=''
PROFILE_LOADED=false
CASE=0
cleanup() {
    local result=$? container
    if [ "$result" -ne 0 ]; then
        for container in "${CONTAINERS[@]}"; do docker logs "$container" >&2 || true; done
    fi
    for container in "${CONTAINERS[@]}"; do docker rm -f "$container" > /dev/null 2>&1 || true; done
    if [ "$PROFILE_LOADED" = true ]; then "${ADMIN[@]}" apparmor_parser -R "$WORK/apparmor.txt" || true; fi
    rm -rf "$WORK"
}
trap cleanup EXIT
ADMIN=()
if [ "$(id -u)" -ne 0 ]; then ADMIN=(sudo); fi
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf '  ok   %s\n' "$1"; }
start() {
    local options=$1 confined=${2:-false}
    CASE=$((CASE + 1))
    PLAYER="${PREFIX}-${CASE}"
    mkdir -p "$WORK/$CASE"
    printf '%s\n' "$options" > "$WORK/$CASE/options.json"
    local args=(--detach --name "$PLAYER" --network none
        --volume "$WORK/$CASE/options.json:/data/options.json:ro")
    if [ "$confined" = true ]; then args+=(--security-opt "apparmor=$PROFILE"); fi
    CONTAINERS+=("$PLAYER")
    docker run "${args[@]}" "$IMAGE" > /dev/null
}
wait_healthy() {
    local attempt
    for ((attempt=0; attempt<150; attempt++)); do
        if [ "$(docker inspect --format '{{.State.Health.Status}}' "$PLAYER")" = healthy ]; then return 0; fi
        [ "$(docker inspect --format '{{.State.Running}}' "$PLAYER")" = true ] || fail 'app stopped during startup'
        sleep 0.2
    done
    fail 'Docker health did not become healthy'
}
wait_log() {
    local text=$1 attempt
    for ((attempt=0; attempt<100; attempt++)); do
        if docker logs "$PLAYER" 2>&1 | grep -F -- "$text" > /dev/null; then return 0; fi
        sleep 0.1
    done
    fail "missing log: $text"
}
stop_cleanly() {
    docker stop --time 5 "$PLAYER" > /dev/null
    [ "$(docker inspect --format '{{.State.ExitCode}}' "$PLAYER")" -ne 137 ] || fail 'shutdown exceeded its deadline'
}
config_line() { docker exec "$PLAYER" cat /run/sendspin-cli/zones/study/config | grep -Fx -- "$1" > /dev/null || fail "missing setting: $1"; }

printf 'Native app startup\n'
labels=$(docker image inspect --format '{{json .Config.Labels}}' "$IMAGE")
version=$(sed -nE 's/^version: "([^"]+)"/\1/p' "$SCRIPT_DIR/../local_audio_zones/config.yaml")
arch=$(docker image inspect --format '{{.Architecture}}' "$IMAGE")
if [ "$arch" = arm64 ]; then arch=aarch64; fi
jq -e --arg version "$version" --arg arch "$arch" '
    .["io.hass.type"] == "app" and .["io.hass.version"] == $version
    and .["io.hass.arch"] == $arch and .["org.opencontainers.image.version"] == $version
' <<< "$labels" > /dev/null || fail 'Home Assistant image labels do not match the manifest'
pass 'Home Assistant image labels match the version and architecture'
start '{"zones":[]}'
wait_healthy
docker exec "$PLAYER" jq -e 'length == 0' /run/sendspin-cli/players.json > /dev/null
# Only route maintenance runs before the user configures their first room.
docker exec "$PLAYER" /bin/bash -euc '[ ! -e /data/zones ]; [ "$(find /run/sendspin-cli/services -mindepth 1 -maxdepth 1 -type d | wc -l)" -eq 1 ]; [ -d /run/sendspin-cli/services/_audio-routes ]'
stop_cleanly
pass 'unconfigured app stays healthy without creating a player'

start '{"log_level":"warning","buffer_ms":250,"zones":[{"id":"study","name":"Study","output":"null","log_level":"debug","buffer_ms":100}]}'
wait_healthy
config_line 'name = Study'
config_line 'id = study'
config_line 'port = 8928'
config_line 'log-level = debug'
config_line 'buffer-ms = 100'
docker exec "$PLAYER" /usr/bin/container-healthcheck
pass 'native options render player identity and per-room overrides'
# Discovery requires both D-Bus and Avahi, even while players are idle.
for service in avahi dbus; do
    docker exec "$PLAYER" s6-svc -d "/run/service/$service"
    sleep 0.5
    if docker exec "$PLAYER" /usr/bin/container-healthcheck > "$WORK/health.log" 2>&1; then
        fail "health ignored stopped $service"
    fi
    grep -F "The bundled $service service is not running." "$WORK/health.log" > /dev/null
    docker exec "$PLAYER" s6-svc -u "/run/service/$service"
done
for service in dbus avahi; do
    docker exec "$PLAYER" timeout 10 s6-svwait -u "/run/service/$service"
done
docker exec "$PLAYER" /usr/bin/container-healthcheck
wait_log 'mdns: advertising _sendspin._tcp'
stop_cleanly
pass 'daemon failure affects health and shutdown remains bounded'

start '{"log_level":"warning","buffer_ms":250,"zones":[{"id":"study","name":"Study","output":"null","port":9000}]}'
wait_healthy
config_line 'port = 9000'
config_line 'log-level = warn'
config_line 'buffer-ms = 250'
stop_cleanly
pass 'explicit player ports and inherited shared settings reach the native player'

printf '\nInvalid startup configuration\n'
for invalid in \
    '{"zones":[{"id":"study","name":"Study","output":"null","port":80}]}' \
    '{"zones":[{"id":"study","name":"Study","device":"/dev/snd/pcmC9999D0c"}]}' \
    '{"zones":[{"id":"study","name":"Study","output":"null"}],"server":"ws://user:s3cr3t@192.0.2.1:8927"}'; do
    start "$invalid"
    exit_code=$(timeout 20 docker wait "$PLAYER")
    [ "$exit_code" -ne 0 ] || fail 'invalid configuration exited successfully'
    logs=$(docker logs "$PLAYER" 2>&1)
    if grep -F 's3cr3t' <<< "$logs" > /dev/null; then fail 'server credentials reached logs'; fi
    if grep -F 'listening on port' <<< "$logs" > /dev/null; then fail 'invalid configuration started a player'; fi
    pass 'invalid settings stop before creating players and keep credentials out of logs'
done

start '{"server":"mdns:","zones":[{"id":"study","name":"Study","output":"null"}]}'
wait_healthy
config_line 'server = mdns:'
wait_log 'Looking for a Sendspin server on _sendspin-server._tcp'
wait_log 'Not advertising _sendspin._tcp'
stop_cleanly
pass 'native mDNS server selection uses discovery without advertising'

stream_hooks() {
    local confined=$1 server marker_directory
    # This literal variable is evaluated by the native player's hook shell.
    # shellcheck disable=SC2016
    start '{"hook_start":"/usr/bin/printf \"native %s hook\\n\" \"$SENDSPIN_EVENT\"","hook_stop":"/usr/bin/printf \"native %s hook\\n\" \"$SENDSPIN_EVENT\"","zones":[{"id":"study","name":"Study","output":"null"}]}' "$confined"
    wait_healthy
    wait_log 'listening on port 8928'
    # The fixture shares only the player's isolated network, not its AppArmor policy.
    server="${PLAYER}-server"
    marker_directory="$WORK/server-$CASE"
    mkdir -p "$marker_directory"
    CONTAINERS+=("$server")
    docker run -d --name "$server" --network "container:$PLAYER" --entrypoint python3 \
        --volume "$SCRIPT_DIR/fake_server.py:/tmp/fake_server.py:ro" \
        --volume "$marker_directory:/fixture" \
        "$IMAGE" /tmp/fake_server.py --marker /fixture/server.marker --port 8928 --connect-timeout 10 > /dev/null
    wait_log 'native start hook'
    wait_log 'native stop hook'
    grep -Fx stream-start "$marker_directory/server.marker" > /dev/null
    grep -Fx stream-end "$marker_directory/server.marker" > /dev/null
    stop_cleanly
}
stream_hooks false
pass 'the native player executes start and stop hooks for an actual stream'

printf '\nAppArmor confinement\n'
if command -v apparmor_parser > /dev/null && [ -r /sys/module/apparmor/parameters/enabled ] && grep -q Y /sys/module/apparmor/parameters/enabled; then
    sed "s/^profile local_audio_zones /profile $PROFILE /" "$SCRIPT_DIR/../local_audio_zones/apparmor.txt" > "$WORK/apparmor.txt"
    "${ADMIN[@]}" apparmor_parser --replace "$WORK/apparmor.txt"
    PROFILE_LOADED=true
    stream_hooks true
    pass 'the shipped AppArmor profile permits native streaming, hooks and clean shutdown'
elif [ "$REQUIRE_APPARMOR" = true ]; then
    fail 'AppArmor enforcement is required but unavailable'
else
    printf '  skip AppArmor enforcement is unavailable on this host\n'
fi
printf '\nNative app smoke checks passed.\n'
