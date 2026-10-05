#!/usr/bin/env bash
# Exercise option validation and player preparation in a disposable Linux container.
# Needs bash and jq. Run as root; the same /run paths as the image are used.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
readonly SCRIPT_DIR
readonly COMMON="$SCRIPT_DIR/../local_audio_zones/rootfs/usr/lib/sendspin-cli/common.sh"
readonly PLAYER_LIBRARY="$SCRIPT_DIR/../local_audio_zones/rootfs/usr/lib/sendspin-cli/players.sh"
readonly PLAYERS=/run/sendspin-cli/players.json

if [ -e "$PLAYERS" ]; then
    printf 'Refusing to replace an existing player manifest; use a disposable container.\n' >&2
    exit 2
fi

WORK=$(mktemp -d)
readonly WORK
trap 'rm -rf "$WORK"; rm -f "$PLAYERS"' EXIT
FAILURES=0
CHECKS=0

check() {
    local description=$1 predicate=$2
    CHECKS=$((CHECKS + 1))
    if jq -e "$predicate" "$WORK/players.json" > /dev/null; then
        printf '  ok   %s\n' "$description"
    else
        printf '  FAIL %s\n' "$description" >&2
        FAILURES=$((FAILURES + 1))
    fi
}

prepare() {
    # The positional arguments belong to the child shell.
    # shellcheck disable=SC2016
    env -u SUPERVISOR_TOKEN -u SENDSPIN_NAME -u SENDSPIN_OUTPUT \
        -u SENDSPIN_LOG_LEVEL -u SENDSPIN_SERVER -u SENDSPIN_BUFFER_MS \
        -u SENDSPIN_AUDIO_FORMAT -u SENDSPIN_ID -u SENDSPIN_HOOK_START \
        -u SENDSPIN_HOOK_STOP -u SENDSPIN_ZONES "$@" \
        bash -euo pipefail -c 'source "$1"; sendspin::prepare_players' bash "$COMMON" \
        > "$WORK/stdout" 2> "$WORK/stderr" || return 1
    cp "$PLAYERS" "$WORK/players.json"
}

reject() {
    local description=$1
    shift
    CHECKS=$((CHECKS + 1))
    if prepare "$@"; then
        printf '  FAIL accepted %s\n' "$description" >&2
        FAILURES=$((FAILURES + 1))
    elif [ ! -s "$WORK/stderr" ]; then
        printf '  FAIL no diagnostic for %s\n' "$description" >&2
        FAILURES=$((FAILURES + 1))
    elif ! cmp -s "$PLAYERS" "$WORK/players.json"; then
        printf '  FAIL invalid configuration replaced the manifest: %s\n' "$description" >&2
        FAILURES=$((FAILURES + 1))
    else
        printf '  ok   refuses %s\n' "$description"
    fi
}

render() {
    # The positional arguments belong to the child shell.
    # shellcheck disable=SC2016
    bash -euo pipefail -c 'source "$1"; sendspin::render_player "$2"' \
        bash "$PLAYER_LIBRARY" "$(jq -c '.[0]' "$WORK/players.json")" \
        > "$WORK/player.conf"
}

printf 'Single-player compatibility\n'
prepare
check 'default player remains Local Audio on default output' \
    'length == 1 and .[0].name == "Local Audio" and .[0].output == "default"'
check 'default discovery and upstream buffering remain unset' \
    '.[0].server == "" and .[0].buffer_ms == ""'
check 'default port stays 8928' '.[0].port == 8928'

prepare 'SENDSPIN_NAME=Old player' 'SENDSPIN_OUTPUT=null' 'SENDSPIN_ID=existing-id'
check 'explicit existing player identity and null backend remain unchanged' \
    '.[0].name == "Old player" and .[0].output == "null" and .[0].client_id == "existing-id"'
render
grep -Fx 'id = existing-id' "$WORK/player.conf" > /dev/null
if grep -q '^port =' "$WORK/player.conf"; then
    printf 'Default player unexpectedly overrides the upstream port.\n' >&2
    exit 1
fi
printf '  ok   single-player configuration keeps its identity and implicit port\n'

prepare 'SENDSPIN_LOG_LEVEL=warning'
check 'Supervisor warning level uses the upstream warn spelling' '.[0].log_level == "warn"'
render
grep -Fx 'log-level = warn' "$WORK/player.conf" > /dev/null

readonly ZONES='[{"id":"study","name":"Study","output":"pulse:ca7_study"},{"id":"guest-room","name":"Guest room","output":"pulse:ca7_guest_room"},{"id":"kids-room","name":"Kids room","output":"pulse:ca7_kids_room"},{"id":"bedroom","name":"Bedroom","output":"pulse:ca7_bedroom","port":9010}]'

printf '\nIndependent zones\n'
prepare "SENDSPIN_ZONES=$ZONES" 'SENDSPIN_LOG_LEVEL=debug' \
    'SENDSPIN_SERVER=mdns:Music Assistant' 'SENDSPIN_BUFFER_MS=250' \
    'SENDSPIN_AUDIO_FORMAT=pcm:48000:16:2' \
    'SENDSPIN_HOOK_START=printf start' 'SENDSPIN_HOOK_STOP=printf stop'
check 'all four named outputs are preserved in order' \
    'map(.name) == ["Study", "Guest room", "Kids room", "Bedroom"] and map(.output) == ["pulse:ca7_study", "pulse:ca7_guest_room", "pulse:ca7_kids_room", "pulse:ca7_bedroom"]'
check 'default and explicit ports are distinct' 'map(.port) == [8928,8929,8930,9010]'
check 'stable zone IDs become independent player identities' \
    'map(.client_id) == ["study","guest-room","kids-room","bedroom"]'
check 'global settings are inherited by every zone' \
    'all(.[]; .log_level == "debug" and .server == "mdns:Music Assistant" and (.buffer_ms | tostring) == "250" and .audio_format == "pcm:48000:16:2" and .hook_start == "printf start" and .hook_stop == "printf stop")'
render
for setting in 'name = Study' 'output = pulse:ca7_study' 'port = 8928' \
    'id = study' 'buffer-ms = 250' 'server = mdns:Music Assistant' \
    'hook-start = printf start' 'hook-stop = printf stop'; do
    grep -Fx "$setting" "$WORK/player.conf" > /dev/null
done
printf '  ok   player configuration contains native output, identity, port and inherited options\n'

printf '\nPer-zone overrides\n'
overrides=$(jq -c '
    .[0] += {log_level:"warning",server:"mdns:Named",buffer_ms:100,
             hook_start:"printf zone-start",hook_stop:"printf zone-stop"}
    | .[1] += {server:"",hook_start:"",hook_stop:""}
' <<< "$ZONES")
prepare "SENDSPIN_ZONES=$overrides" 'SENDSPIN_LOG_LEVEL=debug' \
    'SENDSPIN_SERVER=mdns:Global' 'SENDSPIN_BUFFER_MS=250' \
    'SENDSPIN_HOOK_START=printf global-start' 'SENDSPIN_HOOK_STOP=printf global-stop'
check 'one zone overrides each supported global setting' \
    '.[0].log_level == "warn" and .[0].server == "mdns:Named" and .[0].buffer_ms == "100" and .[0].hook_start == "printf zone-start" and .[0].hook_stop == "printf zone-stop"'
check 'empty optional strings clear an inherited server or hook' \
    '.[1].server == "" and .[1].hook_start == "" and .[1].hook_stop == ""'
check 'other zones keep the shared configuration' \
    'all(.[2:][]; .log_level == "debug" and .server == "mdns:Global" and .buffer_ms == "250" and .hook_start == "printf global-start" and .hook_stop == "printf global-stop")'
render
for setting in 'log-level = warn' 'server = mdns:Named' 'buffer-ms = 100' \
    'hook-start = printf zone-start' 'hook-stop = printf zone-stop'; do
    grep -Fx "$setting" "$WORK/player.conf" > /dev/null
done
printf '  ok   native player configuration renders its zone overrides\n'

prepare "SENDSPIN_ZONES=$(jq -cn '[range(0;32) | {id:("room-"+tostring),name:("Room "+tostring),output:"null"}]')"
check '32 zones are supported with independent identities and ports' \
    'length == 32 and (map(.client_id) | unique | length) == 32 and (map(.port) | unique | length) == 32'

printf '\nInvalid configuration\n'
reject 'malformed JSON' 'SENDSPIN_ZONES=['
reject 'multiple JSON values' 'SENDSPIN_ZONES=[] []'
reject 'whitespace without a JSON value' 'SENDSPIN_ZONES=   '
reject 'object instead of zone list' 'SENDSPIN_ZONES={}'
reject 'non-object zone' 'SENDSPIN_ZONES=["study"]'
reject 'missing ID' 'SENDSPIN_ZONES=[{"name":"Study","output":"null"}]'
reject 'missing name' 'SENDSPIN_ZONES=[{"id":"study","output":"null"}]'
reject 'missing output' 'SENDSPIN_ZONES=[{"id":"study","name":"Study"}]'
reject 'empty name' 'SENDSPIN_ZONES=[{"id":"study","name":"","output":"null"}]'
reject 'whitespace-only name' 'SENDSPIN_ZONES=[{"id":"study","name":"   ","output":"null"}]'
reject 'null output' 'SENDSPIN_ZONES=[{"id":"study","name":"Study","output":null}]'
reject 'unknown zone option' 'SENDSPIN_ZONES=[{"id":"study","name":"Study","output":"null","volume_curve":"linear"}]'
reject 'duplicate IDs' 'SENDSPIN_ZONES=[{"id":"same","name":"A","output":"null"},{"id":"same","name":"B","output":"null"}]'
reject 'duplicate explicit ports' 'SENDSPIN_ZONES=[{"id":"a","name":"A","output":"null","port":9000},{"id":"b","name":"B","output":"null","port":9000}]'
reject 'explicit port colliding with a default' 'SENDSPIN_ZONES=[{"id":"a","name":"A","output":"null","port":8929},{"id":"b","name":"B","output":"null"}]'
reject 'port below range' 'SENDSPIN_ZONES=[{"id":"a","name":"A","output":"null","port":0}]'
reject 'privileged port' 'SENDSPIN_ZONES=[{"id":"a","name":"A","output":"null","port":80}]'
reject 'port above range' 'SENDSPIN_ZONES=[{"id":"a","name":"A","output":"null","port":65536}]'
reject 'fractional port' 'SENDSPIN_ZONES=[{"id":"a","name":"A","output":"null","port":8928.5}]'
reject 'string port' 'SENDSPIN_ZONES=[{"id":"a","name":"A","output":"null","port":"8928"}]'
reject 'path traversal ID' 'SENDSPIN_ZONES=[{"id":"../state","name":"A","output":"null"}]'
reject 'reserved single-player ID' 'SENDSPIN_ZONES=[{"id":"default","name":"A","output":"null"}]'
reject '33 zones' "SENDSPIN_ZONES=$(jq -cn '[range(0;33) | {id:("room-"+tostring),name:"Room",output:"null"}]')"

for field in name output; do
    for control in '\n' '\r' '\t' '\u0000' '\u001b'; do
        reject "$field containing $control" \
            "SENDSPIN_ZONES=[{\"id\":\"study\",\"name\":\"Study\",\"output\":\"null\",\"$field\":\"x${control}server = injected\"}]"
    done
done

reject 'direct server address' "SENDSPIN_ZONES=$ZONES" 'SENDSPIN_SERVER=192.0.2.1:8927'
reject 'unsupported log level' "SENDSPIN_ZONES=$ZONES" 'SENDSPIN_LOG_LEVEL=verbose'
reject 'buffer below range' "SENDSPIN_ZONES=$ZONES" 'SENDSPIN_BUFFER_MS=9'
reject 'buffer above range' "SENDSPIN_ZONES=$ZONES" 'SENDSPIN_BUFFER_MS=2001'
reject 'global hook with a configuration newline' "SENDSPIN_ZONES=$ZONES" $'SENDSPIN_HOOK_START=printf start\nserver = injected'

for field in log_level server buffer_ms hook_start hook_stop; do
    # jq receives the key through --arg, rather than shell interpolation.
    # shellcheck disable=SC2016
    reject "null zone $field" \
        "SENDSPIN_ZONES=$(jq -cn --arg key "$field" '{id:"study",name:"Study",output:"null",($key):null} | [.]')"
done
for buffer in 9 2001 10.5 true '"100"'; do
    reject "invalid zone buffer $buffer" \
        "SENDSPIN_ZONES=[{\"id\":\"study\",\"name\":\"Study\",\"output\":\"null\",\"buffer_ms\":$buffer}]"
done
reject 'invalid zone log level' 'SENDSPIN_ZONES=[{"id":"study","name":"Study","output":"null","log_level":"verbose"}]'
reject 'direct zone server address' 'SENDSPIN_ZONES=[{"id":"study","name":"Study","output":"null","server":"192.0.2.1:8927"}]'
reject 'unsupported zone audio format' 'SENDSPIN_ZONES=[{"id":"study","name":"Study","output":"null","audio_format":"pcm:48000:16:2"}]'
for field in server log_level hook_start hook_stop; do
    # jq receives the key through --arg, rather than shell interpolation.
    # shellcheck disable=SC2016
    reject "control character in zone $field" \
        "SENDSPIN_ZONES=$(jq -cn --arg key "$field" '{id:"study",name:"Study",output:"null",($key):"x\nserver = injected"} | [.]')"
done

printf '\n%d checks, %d failures\n' "$CHECKS" "$FAILURES"
[ "$FAILURES" -eq 0 ]
