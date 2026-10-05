# shellcheck shell=bash

# The implicit player retains its original state and control socket on upgrade.
sendspin::player_paths() {
    if [ "$1" = default ]; then
        player_run_dir=${SENDSPIN_RUN_DIR}
        player_conf=${SENDSPIN_CONF}
        player_state_dir=${SENDSPIN_STATE_DIR}
        player_control_socket=${SENDSPIN_CONTROL_SOCKET}
    else
        player_run_dir="${SENDSPIN_RUN_DIR}/zones/$1"
        player_conf="${player_run_dir}/config"
        player_state_dir="/data/zones/$1/state"
        player_control_socket="${player_run_dir}/control.sock"
    fi
}

sendspin::render_player() {
    jq -r '
        def setting(key; value):
            if value != "" then "\(key) = \(value)" else empty end;
        setting("name"; .name),
        setting("output"; .output),
        setting("log-level"; .log_level),
        "manufacturer = Music Assistant",
        "product-name = Local Audio Zones",
        (if .id != "default" then setting("port"; (.port | tostring)) else empty end),
        setting("buffer-ms"; .buffer_ms),
        setting("audio-format"; .audio_format),
        setting("id"; .client_id),
        setting("hook-start"; .hook_start),
        setting("hook-stop"; .hook_stop),
        setting("server"; .server)
    ' <<< "$1"
}
