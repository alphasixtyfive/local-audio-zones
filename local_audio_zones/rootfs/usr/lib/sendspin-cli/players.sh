# shellcheck shell=bash

sendspin::player_paths() {
    player_run_dir="${SENDSPIN_RUN_DIR}/zones/$1"
    player_conf="${player_run_dir}/config"
    player_state_dir="/data/zones/$1/state"
    player_control_socket="${player_run_dir}/control.sock"
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
        setting("port"; (.port | tostring)),
        setting("buffer-ms"; .buffer_ms),
        setting("id"; .client_id),
        setting("hook-start"; .hook_start),
        setting("hook-stop"; .hook_stop),
        setting("server"; .server)
    ' <<< "$1"
}
