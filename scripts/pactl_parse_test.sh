#!/usr/bin/env bash
# Startup diagnostics against PulseAudio 17 sink JSON; no host audio is used.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
readonly SCRIPT_DIR
source "$SCRIPT_DIR/../local_audio_zones/rootfs/usr/lib/sendspin-cli/common.sh"

# Fields follow native pactl output, including remaps with no active port.
readonly SINKS='[
  {"index":0,"name":"hdmi","description":"HDMI / DisplayPort","mute":false,
   "volume":{"front-left":{"value":48497,"value_percent":"74%"},"front-right":{"value":48497,"value_percent":"74%"}},
   "base_volume":{"value":65536,"value_percent":"100%"},
   "ports":[{"name":"hdmi-output-0","description":"HDMI (unplugged)","availability":"not available"}],
   "active_port":"hdmi-output-0"},
  {"index":7,"name":"ca7_study","description":"Cubilux \"CA7\" FRONT","mute":false,
   "volume":{"front-left":{"value":0,"value_percent":"0%"},"front-right":{"value":0,"value_percent":"0%"}},
   "base_volume":{"value":65536,"value_percent":"100%"},
   "ports":[],"active_port":null,
   "properties":{"Name":"not-the-sink","Mute":"yes"}}
]'

CHECKS=0
check() {
    CHECKS=$((CHECKS + 1))
    if [[ "$2" == *"$3"* ]]; then
        printf '  ok   %s\n' "$1"
    else
        printf '  FAIL %s\nExpected: %s\nActual: %s\n' "$1" "$3" "$2" >&2
        exit 1
    fi
}
absent() {
    CHECKS=$((CHECKS + 1))
    if [[ "$2" != *"$3"* ]]; then
        printf '  ok   %s\n' "$1"
    else
        printf '  FAIL %s\nUnexpected: %s\nActual: %s\n' "$1" "$3" "$2" >&2
        exit 1
    fi
}
report() { sendspin::report_on_sinks "${2:-ca7_study}" "$1" 2>&1; }

out=$(report "$SINKS")
check 'selects the room remap rather than another sink' "$out" 'sink #7, ca7_study'
check 'quoted descriptions survive structured parsing' "$out" 'Cubilux "CA7" FRONT'
check 'base volume is not mistaken for output volume' "$out" 'at 0%'
check 'zero output warns even when a remap has no ports' "$out" 'Host output ca7_study is at zero.'
check 'host level is distinguished from player volume' "$out" 'Music Assistant controls player volume separately.'
absent 'properties cannot impersonate sink fields' "$out" 'not-the-sink'
absent 'remaps with no port produce no port warning' "$out" 'Active port'

fixture=$(jq '.[1].mute = true' <<< "$SINKS")
out=$(report "$fixture")
check 'mute and zero are reported together' "$out" 'is muted and at zero.'
fixture=$(jq '.[1].volume["front-right"].value = 39322' <<< "$SINKS")
out=$(report "$fixture")
check 'one audible channel is not reported as silence' "$out" 'at 60%'
absent 'balance does not trigger a zero warning' "$out" 'is at zero.'
fixture=$(jq '.[1].mute = true' <<< "$fixture")
check 'mute still warns with a nonzero level' "$(report "$fixture")" 'is muted.'
fixture=$(jq '.[1].volume["front-right"].value = 98304' <<< "$SINKS")
check 'levels above 100 percent are preserved' "$(report "$fixture")" 'at 150%'
fixture=$(jq '.[1].volume["front-right"].value = 1' <<< "$SINKS")
absent 'a nonzero level rounded to zero is not called silent' "$(report "$fixture")" 'is at zero.'

out=$(report "$SINKS" hdmi)
check 'selected unavailable port is named' "$out" 'HDMI (unplugged) (hdmi-output-0) is not available'
check 'port warning gives a direct remedy' "$out" 'Check its cable or Home Assistant Audio settings.'
absent 'healthy level does not trigger a mute or zero warning' "$out" 'Host output hdmi is'
for availability in available 'availability unknown'; do
    fixture=$(jq --arg availability "$availability" '.[0].ports[0].availability = $availability' <<< "$SINKS")
    absent "$availability is not reported as unplugged" "$(report "$fixture" hdmi)" 'is not available'
done
fixture=$(jq '.[0].active_port = "other-port"' <<< "$SINKS")
out=$(report "$fixture" hdmi)
check 'missing port metadata falls back to its ID' "$out" 'out of other-port.'
absent 'another port availability cannot trigger the active-port warning' "$out" 'is not available'
fixture=$(jq '.[0].ports[0].description = ""' <<< "$SINKS")
check 'empty port descriptions also fall back to the ID' "$(report "$fixture" hdmi)" 'Active port hdmi-output-0 (hdmi-output-0)'

out=$(report "$SINKS" missing)
check 'missing room output is identified' "$out" 'Configured zone output missing is missing'
check 'missing room cannot silently select the default' "$out" 'will not play through the default output'
check 'available names help correct the configuration' "$out" 'Available outputs: hdmi, ca7_study'
check 'empty sink list is distinguished from a missing room' "$(report '[]')" 'PulseAudio lists no audio outputs'
fixture=$(jq '. + [.[1]]' <<< "$SINKS")
check 'ambiguous sink names are reported safely' "$(report "$fixture")" 'Could not read a unique PulseAudio output'

for invalid in '' 'Connection refused' '{}' '[{"name":"ca7_study"}]'; do
    out=$(report "$invalid")
    check 'malformed output cannot block startup or claim silence' "$out" 'Could not read PulseAudio output diagnostics'
    absent 'malformed output is never claimed audible' "$out" 'Playing through'
done
for mutation in 'del(.[1].volume)' '.[1].volume = {}' '.[1].volume["front-left"].value = null' '.[1].volume["front-left"].value = -1'; do
    fixture=$(jq "$mutation" <<< "$SINKS")
    check 'incomplete levels remain a diagnostic failure' "$(report "$fixture")" 'Could not read PulseAudio output diagnostics'
done
out=$(sendspin::report_on_sinks '' '{}' 2>&1)
[ -z "$out" ]
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cat > "$WORK/pactl" <<'PACTL'
#!/usr/bin/env bash
printf '%s\n' "$PULSE_SERVER"
PACTL
chmod +x "$WORK/pactl"
export PATH="$WORK:$PATH"
unset PULSE_SERVER
check 'diagnostics default to the Supervisor socket' "$(sendspin::pactl info)" 'unix:/run/audio/pulse.sock'
export PULSE_SERVER=unix:/run/audio/native
check 'diagnostics use the configured standalone server' "$(sendspin::pactl info)" "$PULSE_SERVER"
unset PULSE_SERVER
printf '  ok   no target needs no diagnostic\n\n%d diagnostic checks passed\n' "$CHECKS"
