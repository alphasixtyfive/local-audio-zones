# shellcheck shell=bash

readonly SENDSPIN_RUN_DIR=/run/sendspin-cli
readonly SENDSPIN_PLAYERS_FILE=/run/sendspin-cli/players.json
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
        | if (keys - ["zones", "log_level", "server", "buffer_ms", "hook_start", "hook_stop"] | length) > 0 then
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

# Publish only after every player and hardware route has been validated.
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

    if jq -e 'any(.[]; has("device"))' "${temporary}" > /dev/null; then
        resolved=$(mktemp "${SENDSPIN_RUN_DIR}/players.XXXXXX") || { rm -f "${temporary}"; return 1; }
        if ! python3 "${BASH_SOURCE[0]%/*}/audio_routes.py" resolve "${temporary}" > "${resolved}"; then
            rm -f "${temporary}" "${resolved}"
            return 1
        fi
        rm -f "${temporary}"
        temporary=${resolved}
    fi

    if ! chmod 600 "${temporary}" \
        || ! mv -f "${temporary}" "${SENDSPIN_PLAYERS_FILE}"; then
        rm -f "${temporary}"
        return 1
    fi
}

sendspin::decide_daemons() {
    sendspin::read_options || return 1
    sendspin::configured_players > /dev/null || return 1
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
            break
        fi
        sleep 0.1
    done

    return 0
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

sendspin::pulse_sink_names() {
    awk '
        /^\tName:/ {
            sub(/^[^:]*:[[:space:]]*/, "")
            sub(/[[:space:]]+$/, "")
            print
        }
    '
}

sendspin::pulse_sink_state() {
    awk -v target="$1" '
        function value(line) {
            sub(/^[^:]*:[[:space:]]*/, "", line)
            sub(/[[:space:]]+$/, "", line)
            return line
        }

        function loudest(line,   best, pct) {
            best = ""
            while (match(line, /[0-9]+%/)) {
                pct = substr(line, RSTART, RLENGTH - 1) + 0
                if (best == "" || pct > best) best = pct
                line = substr(line, RSTART + RLENGTH)
            }
            return best
        }

        # Descriptions can contain parentheses; availability is in the last pair.
        function last_bracket(s,   i, at) {
            at = 0
            for (i = 1; i <= length(s); i++)
                if (substr(s, i, 1) == "(") at = i
            return at
        }

        function port_line(line,   at, id, rest, bracket, n, part) {
            sub(/^\t\t/, "", line)
            sub(/[[:space:]]+$/, "", line)
            at = index(line, ":")
            if (at == 0) return
            id = substr(line, 1, at - 1)
            rest = substr(line, at + 1)
            sub(/^[[:space:]]+/, "", rest)

            port_description[block, id] = rest
            port_availability[block, id] = ""

            if (rest !~ /\)$/) return
            at = last_bracket(rest)
            if (at == 0) return

            bracket = substr(rest, at + 1, length(rest) - at - 1)
            rest = substr(rest, 1, at - 1)
            sub(/[[:space:]]+$/, "", rest)
            n = split(bracket, part, ",")
            sub(/^[[:space:]]+/, "", part[n])
            sub(/[[:space:]]+$/, "", part[n])

            port_description[block, id] = rest
            port_availability[block, id] = part[n]
        }

        function emit(   description, availability) {
            if (done || name != target \
                || idx == "" || sink_description == "" || mute == "" || level == "")
                return
            description = ""
            availability = ""
            if (port != "") {
                description = port_description[block, port]
                availability = port_availability[block, port]
            }
            print idx; print name; print sink_description; print mute; print level
            print port; print description; print availability
            done = 1
        }

        /^Sink #/ {
            emit()
            block++
            idx = ""; name = ""; sink_description = ""; mute = ""; level = ""
            port = ""; in_ports = 0
            if (match($0, /[0-9]+/)) idx = substr($0, RSTART, RLENGTH)
            next
        }
        /^\tPorts:/  { in_ports = 1; next }
        /^\t[^\t]/   { in_ports = 0 }
        /^\tName:/        { name = value($0); next }
        /^\tDescription:/ { sink_description = value($0); next }
        /^\tMute:/        { mute = value($0); next }
        /^\tVolume:/      { level = loudest($0); next }
        /^\tActive Port:/ { port = value($0); next }

        in_ports && /^\t\t[^\t]/ { port_line($0); next }

        END { emit() }
    '
}

sendspin::warn_if_sink_is_silent() {
    local idx=$1 name=$2 description=$3 mute=$4 level=$5
    local state

    case ${level} in
        '' | *[!0-9]*) return 0 ;;
    esac

    if [ "${mute}" = yes ] && [ "${level}" -eq 0 ]; then
        state='muted and turned down to zero'
    elif [ "${mute}" = yes ]; then
        state='muted'
    elif [ "${level}" -eq 0 ]; then
        state='turned down to zero'
    else
        return 0
    fi

    sendspin::log "The Home Assistant audio output this player plays through is ${state}, so nothing it plays will be heard."
    sendspin::log "That output is sink #${idx}, ${name} (${description})."
    sendspin::log 'Music Assistant volume cannot raise it: that is applied to the audio this player sends, not to the output it sends to.'
    sendspin::log 'Raise it from the Home Assistant host console, or a terminal add-on:'
    sendspin::log "    ha audio volume output --index ${idx} --unmute"
    sendspin::log "    ha audio volume output --index ${idx} --volume 85"
    sendspin::log 'The level is shared with every other add-on on this machine, which is why this one will not set it for you.'
}

sendspin::warn_if_port_is_unavailable() {
    local idx=$1 name=$2 port=$3 description=$4 availability=$5
    local port_text

    [ "${availability}" = 'not available' ] || return 0

    port_text="${port}"
    if [ -n "${description}" ]; then
        port_text="${description} (${port})"
    fi

    sendspin::log "The Home Assistant audio output this player plays through is routed to a socket with nothing plugged into it, so nothing it plays will be heard."
    sendspin::log "That output is sink #${idx}, ${name}, and it is playing out of ${port_text}, which PulseAudio reports as not available."
    sendspin::log 'Music Assistant volume cannot fix it: the audio is reaching an empty socket whatever the level is set to.'
    sendspin::log 'Plug into that socket, or pick an output that is plugged in from the Home Assistant Audio panel.'
    sendspin::log 'The routing is shared with every other add-on on this machine, which is why this one will not change it for you.'
}

sendspin::report_output() {
    local idx=$1 name=$2 description=$3 level=$4 port=$5 port_description=$6
    local out

    out="Playing through sink #${idx}, ${name} (${description}), at ${level}%"
    if [ -n "${port_description}" ] && [ -n "${port}" ]; then
        out="${out}, out of ${port_description} (${port})"
    elif [ -n "${port}" ]; then
        out="${out}, out of ${port}"
    fi
    sendspin::log "${out}."
}

sendspin::report_on_sinks() {
    local target=$1 sinks=$2
    local state name
    local -a field names

    if [ -z "${sinks}" ]; then
        sendspin::log "PulseAudio lists no audio outputs. This zone will wait for ${target}."
        return 0
    fi
    [ -n "${target}" ] || return 0
    state=$(sendspin::pulse_sink_state "${target}" <<< "${sinks}") || return 0
    if [ -z "${state}" ]; then
        mapfile -t names < <(sendspin::pulse_sink_names <<< "${sinks}")
        for name in "${names[@]}"; do
            if [ "${name}" = "${target}" ]; then
                sendspin::log "The zone output ${target} is listed by PulseAudio but could not be read."
                return 0
            fi
        done
        sendspin::log "The configured zone output ${target} is not among the outputs PulseAudio lists."
        sendspin::log 'This zone will wait for its configured output; it will not play through the default output.'
        sendspin::log 'The outputs it does list are:'
        for name in "${names[@]}"; do
            sendspin::log "    ${name}"
        done
        sendspin::log "Check this zone's output setting, or restore its configured PulseAudio output."
        return 0
    fi

    mapfile -t field <<< "${state}"
    [ "${#field[@]}" -ge 5 ] && [ "${#field[@]}" -le 8 ] || return 0

    sendspin::report_output "${field[0]}" "${field[1]}" "${field[2]}" "${field[4]}" \
        "${field[5]:-}" "${field[6]:-}"
    sendspin::warn_if_sink_is_silent \
        "${field[0]}" "${field[1]}" "${field[2]}" "${field[3]}" "${field[4]}"
    sendspin::warn_if_port_is_unavailable \
        "${field[0]}" "${field[1]}" "${field[5]:-}" "${field[6]:-}" "${field[7]:-}"
}

sendspin::check_output_is_audible() {
    local sinks zone name target
    [ -S "${PULSE_SOCKET}" ] || return 0
    sinks=$(sendspin::pactl list sinks) || return 0
    while IFS= read -r zone; do
        name=$(jq -r '.name' <<< "${zone}") || return 0
        target=$(jq -r '.output[6:]' <<< "${zone}") || return 0
        sendspin::log "Zone ${name}:"
        sendspin::report_on_sinks "${target}" "${sinks}" zone
    done < <(jq -c '.[] | select(.output | startswith("pulse:") and length > 6)' "${SENDSPIN_PLAYERS_FILE}")
}
