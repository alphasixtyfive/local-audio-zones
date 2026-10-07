# Modified for Local Audio Zones; upstream attribution is in NOTICE.
# shellcheck shell=bash

readonly SENDSPIN_RUN_DIR=/run/sendspin-cli
readonly SENDSPIN_PLAYERS_FILE=/run/sendspin-cli/players.json
readonly SENDSPIN_SELECTORS_FILE=/run/sendspin-cli/selectors.json
readonly SENDSPIN_DAEMON_DECISION=/run/sendspin-cli/bundled-daemons
readonly SYSTEM_BUS_SOCKET=/var/run/dbus/system_bus_socket
readonly AVAHI_SOCKET=/run/avahi-daemon/socket
readonly PULSE_SOCKET=/run/audio/pulse.sock

sendspin::log() {
    printf '%s\n' "$*" >&2
}

# Supervisor owns this file; the app only reads its native configuration.
sendspin::read_options() {
    local config
    config=$(jq --slurp -ce '
        def optional_type(key; expected):
            if .[key] == null or (.[key] | type) == expected then .
            else error(key + " must be a " + expected) end;
        if length != 1 or (.[0] | type) != "object" then
            error("options must contain one JSON object") else .[0] end
        | if (keys - ["zones", "usb_relays", "log_level", "server", "buffer_ms", "hook_start", "hook_stop"] | length) > 0 then
            error("unknown app option") else . end
        | optional_type("log_level"; "string")
        | optional_type("server"; "string")
        | optional_type("buffer_ms"; "number")
        | optional_type("hook_start"; "string")
        | optional_type("hook_stop"; "string")
    ' /data/options.json) || return 1
    SENDSPIN_ZONES=$(jq -c 'if has("zones") and .zones != null then .zones else [] end' <<< "${config}")
    SENDSPIN_LOG_LEVEL=$(jq -r '.log_level // "info"' <<< "${config}")
    SENDSPIN_SERVER=$(sendspin::option "${config}" server)
    SENDSPIN_BUFFER_MS=$(sendspin::option "${config}" buffer_ms)
    SENDSPIN_HOOK_START=$(sendspin::option "${config}" hook_start)
    SENDSPIN_HOOK_STOP=$(sendspin::option "${config}" hook_stop)
}

sendspin::configured_players() {
    jq --slurp --arg log_level "${SENDSPIN_LOG_LEVEL}" \
        --arg server "${SENDSPIN_SERVER}" \
        --arg buffer_ms "${SENDSPIN_BUFFER_MS}" \
        --arg hook_start "${SENDSPIN_HOOK_START}" \
        --arg hook_stop "${SENDSPIN_HOOK_STOP}" \
        -f "${BASH_SOURCE[0]%/*}/players.jq" \
        <<< "${SENDSPIN_ZONES}"
}

# Publish validated selectors and fixed player outputs before starting services.
sendspin::prepare_players() {
    local temporary resolved

    sendspin::read_options || return 1
    mkdir -p "${SENDSPIN_RUN_DIR}" || return 1
    temporary=$(mktemp "${SENDSPIN_RUN_DIR}/players.XXXXXX") || return 1

    if ! sendspin::configured_players > "${temporary}"; then
        rm -f "${temporary}"
        sendspin::log 'Could not prepare the player configuration. Check the app Configuration tab.'
        return 1
    fi

    if ! python3 "${BASH_SOURCE[0]%/*}/usb_triggers.py" validate --players "${temporary}"; then
        rm -f "${temporary}"
        return 1
    fi

    # Keep the validated selectors for hotplug recovery; players use fixed outputs.
    resolved=$(mktemp "${SENDSPIN_RUN_DIR}/players.XXXXXX") || { rm -f "${temporary}"; return 1; }
    if ! python3 "${BASH_SOURCE[0]%/*}/audio_routes.py" prepare "${temporary}" > "${resolved}"; then
        rm -f "${temporary}" "${resolved}"
        return 1
    fi
    if ! chmod 600 "${temporary}" "${resolved}" \
        || ! mv -f "${temporary}" "${SENDSPIN_SELECTORS_FILE}" \
        || ! mv -f "${resolved}" "${SENDSPIN_PLAYERS_FILE}"; then
        rm -f "${temporary}"
        rm -f "${resolved}"
        return 1
    fi
}

sendspin::decide_daemons() {
    local configured
    sendspin::read_options || return 1
    configured=$(sendspin::configured_players) || return 1
    python3 "${BASH_SOURCE[0]%/*}/usb_triggers.py" validate --players <(printf '%s\n' "${configured}") || return 1
    mkdir -p "${SENDSPIN_RUN_DIR}"
    if jq -e 'length == 0' <<< "${SENDSPIN_ZONES}" > /dev/null; then
        printf 'no\n' > "${SENDSPIN_DAEMON_DECISION}"
    else
        printf 'yes\n' > "${SENDSPIN_DAEMON_DECISION}"
        sendspin::log 'Starting the bundled dbus and avahi-daemon for mDNS.'
    fi
}

sendspin::daemon_decision() {
    cat "${SENDSPIN_DAEMON_DECISION}"
}

# Avahi needs D-Bus to be listening, not just started by s6.
sendspin::wait_for_socket() {
    local i

    for ((i = 0; i < 100; i++)); do
        if [ -S "$1" ]; then
            return 0
        fi
        sleep 0.1
    done

    sendspin::log "Timed out waiting for service socket $1."
    return 1
}

sendspin::option() {
    jq -r --arg key "$2" '.[$key] // empty' <<< "$1"
}

sendspin::redact_server() {
    printf '%s\n' "$1" | sed -e 's|://[^/@]*@|://|' -e 's|^[^:/@]*:[^/@]*@||'
}

# Diagnostics never change the host's levels or selected ports.
sendspin::pactl() {
    LC_ALL=C PULSE_SERVER="unix:${PULSE_SOCKET}" timeout 3 pactl "$@" 2> /dev/null
}

sendspin::report_on_sinks() {
    local target=$1 sinks=$2 report
    [ -n "${target}" ] || return 0
    report=$(jq -er --arg target "${target}" '
        if type != "array" then error("expected sinks") else . end
        | map(select(.name == $target)) as $matching
        | if length == 0 then
            "PulseAudio lists no audio outputs. Restore the configured zone output \($target)."
          elif ($matching | length) == 0 then
            "Configured zone output \($target) is missing; it will not play through the default output.",
            "Check the room output setting or restore its PulseAudio output.",
            ("Available outputs: " + (map(.name) | join(", ")))
          elif ($matching | length) != 1 then
            "Could not read a unique PulseAudio output for \($target)."
          else
            $matching[0]
            | if (.index | type) != "number" or (.description | type) != "string"
                 or (.mute | type) != "boolean" or (.volume | type) != "object" then
                error("incomplete sink") else . end
            | [.volume[].value] as $levels
            | if ($levels | length) == 0 or any($levels[]; type != "number" or . < 0) then
                error("incomplete volume") else . end
            | ($levels | max) as $level
            | . as $sink
            | ([.ports[]? | select(.name == $sink.active_port)][0] // {}) as $port
            | ($port.description | if type == "string" and length > 0 then . else $sink.active_port end) as $port_name
            | ("Playing through sink #\(.index), \(.name) (\(.description)), at "
               + (($level / 65536 * 100 + 0.5 | floor) | tostring) + "%"
               + (if (.active_port // "") == "" then ""
                  else ", out of " + $port_name end) + "."),
              (if .mute or $level == 0 then
                  "Host output \(.name) is "
                  + (if .mute and $level == 0 then "muted and at zero"
                     elif .mute then "muted" else "at zero" end) + ".",
                  "Adjust this shared output in Home Assistant Audio settings; Music Assistant controls player volume separately."
               else empty end),
              (if $port.availability == "not available" then
                  "Active port \($port_name) (\($sink.active_port)) is not available. Check its cable or Home Assistant Audio settings."
               else empty end)
          end
    ' <<< "${sinks}" 2> /dev/null) || {
        sendspin::log "Could not read PulseAudio output diagnostics for ${target}."
        return 0
    }
    sendspin::log "${report}"
}

sendspin::check_output_is_audible() {
    local sinks zone name target
    [ -S "${PULSE_SOCKET}" ] || return 0
    sinks=$(sendspin::pactl --format=json list sinks) || return 0
    while IFS= read -r zone; do
        name=$(jq -r '.name' <<< "${zone}") || return 0
        target=$(jq -r '.output[6:]' <<< "${zone}") || return 0
        sendspin::log "Zone ${name}:"
        sendspin::report_on_sinks "${target}" "${sinks}"
    done < <(jq -c '.[] | select(.output | startswith("pulse:") and length > 6)' "${SENDSPIN_PLAYERS_FILE}")
}
