def text_value($label):
    if type != "string" then error($label + " must be a string")
    elif test("[\u0000-\u001f\u007f-\u009f]") then
        error($label + " must not contain control characters")
    else . end;

def nonempty_text($label):
    text_value($label)
    | if test("^\\s*$") then error($label + " must not be empty") else . end;

def log_value($label):
    text_value($label)
    | if . == "warning" then "warn"
      elif . == "debug" or . == "info" or . == "warn" or . == "error" then .
      else error($label + " must be debug, info, warning, warn or error") end;

def server_value($label):
    text_value($label)
    | if . == "" or startswith("mdns:") then .
      else error($label + " must be empty, mdns: or mdns:<name>") end;

def buffer_value($label):
    if type != "number" then error($label + " must be a number")
    elif . < 10 or . > 2000 or . != floor then
        error($label + " must be a whole number between 10 and 2000")
    else tostring end;

def globals:
    {
        log_level: ($log_level | log_value("log_level")),
        server: ($server | server_value("server")),
        buffer_ms: ($buffer_ms | text_value("buffer_ms")),
        audio_format: ($audio_format | text_value("audio_format")),
        hook_start: ($hook_start | text_value("hook_start")),
        hook_stop: ($hook_stop | text_value("hook_stop"))
    };

if length != 1 then error("zones must contain exactly one JSON value") else .[0] end
| if type != "array" then error("zones must be an array")
elif length > 32 then error("zones must contain at most 32 players")
elif length == 0 then
    [{
        id: "default",
        name: ($name | nonempty_text("name")),
        output: ($output | nonempty_text("output")),
        port: 8928,
        client_id: ($client_id | text_value("id"))
    } + globals]
else
    to_entries
    | map(
        .key as $index
        | .value
        | ("zones[" + ($index | tostring) + "]") as $label
        | if type != "object" then error($label + " must be an object") else . end
        | if (keys - ["id", "name", "output", "port", "log_level", "server",
                      "buffer_ms", "hook_start", "hook_stop"] | length) > 0 then
            error($label + " contains an unknown option")
          else . end
        | .id |= nonempty_text($label + ".id")
        | if .id == "default" then error($label + ".id 'default' is reserved for single-player mode") else . end
        | if (.id | test("^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")) then .
          else error($label + ".id must contain 1 to 64 letters, digits, underscores or hyphens and start with a letter or digit") end
        | .name |= nonempty_text($label + ".name")
        | .output |= nonempty_text($label + ".output")
        | .port = (if has("port") then .port else 8928 + $index end)
        | if (.port | type) != "number" then error($label + ".port must be a number")
          elif .port < 1024 or .port > 65535 or .port != (.port | floor) then
            error($label + ".port must be a whole number between 1024 and 65535")
          else . end
        | if has("buffer_ms") then .buffer_ms |= buffer_value($label + ".buffer_ms") else . end
        | globals + .
        | .log_level |= log_value($label + ".log_level")
        | .server |= server_value($label + ".server")
        | .hook_start |= text_value($label + ".hook_start")
        | .hook_stop |= text_value($label + ".hook_stop")
        | . + {client_id: .id}
    )
    | if (map(.id) | unique | length) != length then error("zone ids must be unique")
      elif (map(.port) | unique | length) != length then error("zone ports must be unique")
      else . end
end
