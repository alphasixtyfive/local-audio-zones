#!/usr/bin/env bash
# Exercise the shipped s6 tree with a stand-in player, without network or audio.
# Usage: scripts/multi_zone_supervision_test.sh <built-image>
set -euo pipefail

if [ "$#" -ne 1 ]; then
    printf 'usage: %s <built-image>\n' "$0" >&2
    exit 2
fi
readonly IMAGE=$1
WORK=$(mktemp -d)
readonly WORK
readonly CONTAINER="local-audio-zones-$$"
readonly SCANNER_CONTAINER="${CONTAINER}-scanner"

cleanup() {
    local status=$?
    if [ "$status" -ne 0 ]; then
        docker logs "$CONTAINER" >&2 || true
        docker logs "$SCANNER_CONTAINER" >&2 || true
    fi
    docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
    docker rm -f "$SCANNER_CONTAINER" > /dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

cat > "$WORK/player" <<'PLAYER'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = status ]; then
    [ "$#" -eq 3 ] && [ "$2" = --control-socket ] && [ -e "$3" ]
    exit $?
fi
config=''
state=''
socket=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        --config) config=$2; shift 2 ;;
        --state-dir) state=$2; shift 2 ;;
        --control-socket) socket=$2; shift 2 ;;
        *) printf 'unexpected player argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done
name=$(sed -n 's/^name = //p' "$config")
mkdir -p "$state"
touch "$socket"
printf '%s\n' "$$" > "$state/pid"
printf '%s\n' "$config" > "$state/config-path"
printf '%s\n' "$socket" > "$state/socket-path"
printf 'started %s %s\n' "$name" "$$"
trap 'printf "stopped %s %s\n" "$name" "$$"; exit 0' TERM INT
while :; do sleep 0.1; done
PLAYER
chmod +x "$WORK/player"

docker run -d --name "$CONTAINER" --network none --no-healthcheck \
    --volume "$WORK/player:/usr/bin/sendspin-cli:ro" \
    --env 'SENDSPIN_ZONES=[{"id":"study","name":"Study","output":"null"},{"id":"guest","name":"Guest room","output":"null"}]' \
    --entrypoint /bin/bash "$IMAGE" -euc '
        mkdir -p /run/sendspin-cli
        printf "no\n" > /run/sendspin-cli/bundled-daemons
        exec bash /etc/s6-overlay/s6-rc.d/sendspin-cli/run
    ' > /dev/null

wait_for_player() {
    local id=$1 previous=${2:-} container=${3:-$CONTAINER} current='' attempt
    for ((attempt=0; attempt<100; attempt++)); do
        current=$(docker exec "$container" cat "/data/zones/$id/state/pid" 2> /dev/null || true)
        if [ -n "$current" ] && [ "$current" != "$previous" ]; then
            printf '%s' "$current"
            return 0
        fi
        sleep 0.1
    done
    printf 'Player %s did not start or recover.\n' "$id" >&2
    return 1
}

study_pid=$(wait_for_player study)
guest_pid=$(wait_for_player guest)
[ "$study_pid" != "$guest_pid" ]
printf '  ok   two independently supervised players start\n'

study_socket=$(docker exec "$CONTAINER" cat /data/zones/study/state/socket-path)
guest_socket=$(docker exec "$CONTAINER" cat /data/zones/guest/state/socket-path)
[ "$study_socket" != "$guest_socket" ]
[ "$study_socket" = /run/sendspin-cli/zones/study/control.sock ]
[ "$guest_socket" = /run/sendspin-cli/zones/guest/control.sock ]
printf '  ok   state and control paths belong to their zones\n'

docker exec "$CONTAINER" /usr/bin/container-healthcheck
docker exec "$CONTAINER" rm "$guest_socket"
if docker exec "$CONTAINER" /usr/bin/container-healthcheck > "$WORK/health.log" 2>&1; then
    printf 'Health check accepted an unresponsive second zone.\n' >&2
    exit 1
fi
grep -F 'Player guest is not responding' "$WORK/health.log" > /dev/null
docker exec "$CONTAINER" touch "$guest_socket"
docker exec "$CONTAINER" /usr/bin/container-healthcheck
printf '  ok   the health check includes every zone and recovers with it\n'

docker exec "$CONTAINER" /bin/bash -c \
    'printf "saved volume\n" > /data/zones/study/state/persisted-state'
# The minimal image provides kill as a Bash builtin.
# shellcheck disable=SC2016
docker exec "$CONTAINER" /bin/bash -c 'kill -KILL "$1"' bash "$study_pid"
recovered_pid=$(wait_for_player study "$study_pid")
[ "$recovered_pid" != "$study_pid" ]
[ "$(docker exec "$CONTAINER" cat /data/zones/study/state/persisted-state)" = 'saved volume' ]
[ "$(docker exec "$CONTAINER" cat /data/zones/guest/state/pid)" = "$guest_pid" ]
# shellcheck disable=SC2016
docker exec "$CONTAINER" /bin/bash -c 'kill -0 "$1"' bash "$guest_pid"
printf '  ok   a failed zone recovers without restarting its neighbor\n'

docker stop --time 5 "$CONTAINER" > /dev/null
logs=$(docker logs "$CONTAINER" 2>&1)
grep -F "stopped Study $recovered_pid" <<< "$logs" > /dev/null
grep -F "stopped Guest room $guest_pid" <<< "$logs" > /dev/null
[ "$(docker inspect --format '{{.State.ExitCode}}' "$CONTAINER")" -ne 137 ]
printf '  ok   container shutdown stops both players before the kill deadline\n'

# This case keeps the image's full init tree: the scanner must not be PID 1.
docker run -d --name "$SCANNER_CONTAINER" --network none --no-healthcheck \
    --volume "$WORK/player:/usr/bin/sendspin-cli:ro" \
    --env 'SENDSPIN_ZONES=[{"id":"study","name":"Study","output":"null"},{"id":"guest","name":"Guest room","output":"null"}]' \
    "$IMAGE" > /dev/null
wait_for_player study '' "$SCANNER_CONTAINER" > /dev/null
wait_for_player guest '' "$SCANNER_CONTAINER" > /dev/null

# Match the generated services directory, leaving the outer s6 scanner alone.
# shellcheck disable=SC2016
docker exec "$SCANNER_CONTAINER" /bin/bash -euc '
    for path in /proc/[0-9]*/cmdline; do
        arguments=()
        mapfile -d "" -t arguments 2>/dev/null < "$path" || continue
        command=${arguments[0]:-}
        if [ "${command##*/}" = s6-svscan ] \
            && [ "${arguments[1]:-}" = /run/sendspin-cli/services ]; then
            pid=${path#/proc/}
            pid=${pid%/cmdline}
            [ "$pid" -ne 1 ]
            kill -KILL "$pid"
            exit 0
        fi
    done
    printf "Could not find the player scanner.\n" >&2
    exit 1
'
exit_code=$(timeout 15 docker wait "$SCANNER_CONTAINER")
[ "$exit_code" -eq 137 ]
logs=$(docker logs "$SCANNER_CONTAINER" 2>&1)
grep -F 'sendspin-cli exited 137; stopping the container.' <<< "$logs" > /dev/null
[ "$(grep -c '^started Study ' <<< "$logs")" -eq 1 ]
[ "$(grep -c '^started Guest room ' <<< "$logs")" -eq 1 ]
[ "$(docker inspect --format '{{.State.Pid}}' "$SCANNER_CONTAINER")" -eq 0 ]
printf '  ok   unexpected scanner death halts the container without duplicate players\n'
