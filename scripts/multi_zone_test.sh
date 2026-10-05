#!/usr/bin/env bash
# Test native options and player rendering in a disposable Linux container.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
readonly SCRIPT_DIR
readonly COMMON="$SCRIPT_DIR/../local_audio_zones/rootfs/usr/lib/sendspin-cli/common.sh"
readonly LIBRARY="$SCRIPT_DIR/../local_audio_zones/rootfs/usr/lib/sendspin-cli/players.sh"
readonly PLAYERS=/run/sendspin-cli/players.json OPTIONS=/data/options.json
if [ -e "$PLAYERS" ] || [ -e "$OPTIONS" ]; then
    printf 'Use a disposable container without existing options or players.\n' >&2
    exit 2
fi
WORK=$(mktemp -d)
readonly WORK
cleanup() {
    local result=$?
    if [ "$result" -ne 0 ] && [ -e "$WORK/stderr" ]; then cat "$WORK/stderr" >&2; fi
    rm -rf "$WORK"
    rm -f "$PLAYERS" "$OPTIONS"
}
trap cleanup EXIT
mkdir -p /data
CHECKS=0
FAILURES=0
prepare() {
    printf '%s\n' "$1" > "$OPTIONS"
    # Positional arguments belong to the child shell.
    # shellcheck disable=SC2016
    bash -euo pipefail -c 'source "$1"; sendspin::prepare_players' bash "$COMMON" > "$WORK/stdout" 2> "$WORK/stderr" || return 1
    cp "$PLAYERS" "$WORK/players.json"
}
check() {
    CHECKS=$((CHECKS + 1))
    if jq -e "$2" "$WORK/players.json" > /dev/null; then
        printf '  ok   %s\n' "$1"
    else
        printf '  FAIL %s\n' "$1" >&2
        FAILURES=$((FAILURES + 1))
    fi
}
reject() {
    CHECKS=$((CHECKS + 1))
    if prepare "$2"; then
        printf '  FAIL accepted %s\n' "$1" >&2
        FAILURES=$((FAILURES + 1))
    elif [ ! -s "$WORK/stderr" ] || ! cmp -s "$PLAYERS" "$WORK/players.json"; then
        printf '  FAIL missing diagnostic or changed manifest: %s\n' "$1" >&2
        FAILURES=$((FAILURES + 1))
    else
        printf '  ok   refuses %s\n' "$1"
    fi
}
reject_zone() { reject "$1" "{\"zones\":[{\"id\":\"study\",\"name\":\"Study\",\"output\":\"null\",$2}]}"; }
render() {
    # shellcheck disable=SC2016
    bash -euo pipefail -c 'source "$1"; sendspin::render_player "$2"' bash "$LIBRARY" "$(jq -c '.[0]' "$WORK/players.json")" > "$WORK/player.conf"
}
printf 'Native configuration\n'
prepare '{"zones":[]}'
check 'empty zones creates no implicit player' 'length == 0'
prepare '{}'
check 'unconfigured app waits without a ghost player' 'length == 0'
readonly ZONES='[{"id":"study","name":"Study","output":"pulse:existing_front"},{"id":"guest-room","name":"Guest room","output":"pulse:guest"},{"id":"kids-room","name":"Kids room","output":"pulse:kids"},{"id":"bedroom","name":"Bedroom","output":"pulse:bedroom","port":9010}]'
prepare "$(jq -cn --argjson zones "$ZONES" '{zones:$zones,log_level:"debug",server:"mdns:Music Assistant",buffer_ms:250,hook_start:"printf start",hook_stop:"printf stop"}')"
check 'all four rooms retain their names and outputs' 'map(.name) == ["Study","Guest room","Kids room","Bedroom"] and .[0].output == "pulse:existing_front"'
check 'explicit and automatic ports are distinct' 'map(.port) == [8928,8929,8930,9010]'
check 'IDs are the native player identities' 'map(.client_id) == ["study","guest-room","kids-room","bedroom"]'
check 'all rooms inherit shared settings' 'all(.[]; .log_level == "debug" and .server == "mdns:Music Assistant" and .buffer_ms == "250" and .hook_start == "printf start" and .hook_stop == "printf stop")'
render
for setting in 'name = Study' 'output = pulse:existing_front' 'port = 8928' 'id = study' 'buffer-ms = 250' 'server = mdns:Music Assistant' 'hook-start = printf start' 'hook-stop = printf stop'; do
    grep -Fx "$setting" "$WORK/player.conf" > /dev/null
done
prepare "$(jq -cn --argjson zones "$ZONES" '{zones:($zones | .[0] += {log_level:"warning",server:"mdns:Named",buffer_ms:100,hook_start:"printf zone-start",hook_stop:"printf zone-stop"} | .[1] += {server:"",hook_start:"",hook_stop:""}),log_level:"debug",server:"mdns:Global",buffer_ms:250,hook_start:"printf global-start",hook_stop:"printf global-stop"}')"
check 'per-room overrides normalize warning to warn' '.[0].log_level == "warn" and .[0].server == "mdns:Named" and .[0].buffer_ms == "100" and .[0].hook_start == "printf zone-start" and .[0].hook_stop == "printf zone-stop"'
check 'empty strings clear inherited discovery or hooks' '.[1].server == "" and .[1].hook_start == "" and .[1].hook_stop == ""'
check 'remaining rooms inherit shared settings' 'all(.[2:][]; .log_level == "debug" and .server == "mdns:Global" and .buffer_ms == "250")'
render
grep -Fx 'log-level = warn' "$WORK/player.conf" > /dev/null
prepare "$(jq -cn '{zones:[range(0;32) | {id:("room-"+tostring),name:"Room",output:("pulse:room-"+tostring)}]}')"
check '32 rooms have independent IDs and ports' 'length == 32 and (map(.client_id) | unique | length) == 32 and (map(.port) | unique | length) == 32'
# Validate device schema separately from hardware; routing has its own private fixture.
printf '%s\n' '{"zones":[{"id":"a","name":"A","device":"/dev/snd/by-id/usb-card"},{"id":"b","name":"B","device":"/dev/snd/pcmC4D0p","channel_pair":"rear"}]}' > "$OPTIONS"
# shellcheck disable=SC2016
bash -euo pipefail -c 'source "$1"; sendspin::read_options; sendspin::configured_players' bash "$COMMON" > "$WORK/devices.json"
jq -e '.[0].channel_pair == "front" and .[1].channel_pair == "rear" and all(.[]; has("device") and (has("output") | not))' "$WORK/devices.json" > /dev/null
printf '  ok   device selectors preserve explicit pairs and default to front\n'
printf '\nInvalid settings leave the previous manifest intact\n'
reject 'malformed JSON' '['
reject 'concatenated documents' '{} {}'
reject 'non-object options' '[]'
reject 'false zones' '{"zones":false}'
reject 'object zones' '{"zones":{}}'
reject 'non-object room' '{"zones":["study"]}'
reject 'missing ID' '{"zones":[{"name":"Study","output":"null"}]}'
reject 'missing name' '{"zones":[{"id":"study","output":"null"}]}'
reject 'missing device and output' '{"zones":[{"id":"study","name":"Study"}]}'
reject_zone 'empty name' '"name":""'
reject_zone 'whitespace name' '"name":"   "'
reject_zone 'null output' '"output":null'
reject_zone 'unknown option' '"curve":"linear"'
reject 'duplicate IDs' '{"zones":[{"id":"a","name":"A","output":"null"},{"id":"a","name":"B","output":"null"}]}'
reject 'duplicate explicit ports' '{"zones":[{"id":"a","name":"A","output":"null","port":9000},{"id":"b","name":"B","output":"pulse:other","port":9000}]}'
reject 'port assignment collision' '{"zones":[{"id":"a","name":"A","output":"null","port":8929},{"id":"b","name":"B","output":"pulse:other"}]}'
for port in 0 80 65536 8928.5 '"8928"'; do reject_zone "invalid port $port" "\"port\":$port"; done
reject 'duplicate explicit outputs' '{"zones": [{"id":"a","name":"A","output":"pulse:shared"},{"id":"b","name":"B","output":"pulse:shared"}]}'
reject_zone 'path traversal ID' '"id":"../state"'
reject '33 rooms' "$(jq -cn '{zones:[range(0;33) | {id:("room-"+tostring),name:"Room",output:("pulse:room-"+tostring)}]}')"
reject_zone 'device and output together' '"device":"/dev/snd/controlC4"'
reject_zone 'pair without device' '"channel_pair":"front"'
for device in '/dev/snd/pcmC4D0c' '/dev/snd/seq' '/dev/snd/timer' '/tmp/controlC4' '/dev/snd/by-id/../../other' ''; do
    reject "invalid device $device" "$(jq -cn --arg device "$device" '{zones:[{id:"a",name:"A",device:$device}]}')"
done
for pair in '"all"' '"stereo"' null 2; do
    reject "invalid pair $pair" "{\"zones\":[{\"id\":\"a\",\"name\":\"A\",\"device\":\"/dev/snd/controlC4\",\"channel_pair\":$pair}]}"
done
for field in name output; do
    for control in '\n' '\r' '\t' '\u0000' '\u001b'; do reject_zone "$field containing $control" "\"$field\":\"x${control}server = injected\""; done
done
for field in log_level server buffer_ms hook_start hook_stop; do reject_zone "null room $field" "\"$field\":null"; done
for buffer in 9 2001 10.5 true '"100"'; do reject_zone "invalid room buffer $buffer" "\"buffer_ms\":$buffer"; done
reject_zone 'direct server address' '"server":"192.0.2.1:8927"'
reject_zone 'unsupported log level' '"log_level":"verbose"'
reject_zone 'audio format override' '"audio_format":"pcm:48000:16:2"'
for field in server log_level hook_start hook_stop; do reject_zone "control character in $field" "\"$field\":\"x\nserver = injected\""; done
for buffer in 9 2001 10.5 true '"100"'; do reject "invalid shared buffer $buffer" "{\"zones\":$ZONES,\"buffer_ms\":$buffer}"; done
reject 'obsolete global player name' '{"name":"Implicit player","zones":[]}'
reject 'shared direct server address' "{\"zones\":$ZONES,\"server\":\"ws://user:secret@192.0.2.1:8927\"}"
reject 'shared hook injection' "{\"zones\":$ZONES,\"hook_start\":\"printf start\nserver = injected\"}"
printf '\n%d checks, %d failures\n' "$CHECKS" "$FAILURES"
[ "$FAILURES" -eq 0 ]
