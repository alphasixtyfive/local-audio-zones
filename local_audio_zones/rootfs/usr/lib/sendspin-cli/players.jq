# Modified for Local Audio Zones; upstream attribution is in NOTICE.
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

def pair_value($label):
    if . == "Front left/right" then "front"
    elif . == "Rear left/right" then "rear"
    elif . == "Side left/right" then "side"
    elif . == "Centre/Subwoofer" then "center_sub"
    else error($label + " must select a standard output pair") end;

def channels_value($label):
    text_value($label)
    | split(",") | map(gsub("^\\s+|\\s+$"; ""))
    | if length != 2 then error($label + " must contain exactly two comma-separated channels")
      elif any(.[]; test("^[a-z][a-z0-9-]*$") | not) then
        error($label + " must contain lowercase channel names, such as aux0,aux1")
      elif (unique | length) != 2 then error($label + " must contain two distinct channels")
      else . end;

def globals:
    {
        log_level: ($log_level | log_value("log_level")),
        server: ($server | server_value("server")),
        buffer_ms: ($buffer_ms | if . == "" then . else tonumber | buffer_value("buffer_ms") end),
        hook_start: ($hook_start | text_value("hook_start")),
        hook_stop: ($hook_stop | text_value("hook_stop"))
    };

globals as $settings
| if length != 1 then error("zones must contain exactly one JSON value") else .[0] end
| if type != "array" then error("zones must be an array")
elif length > 32 then error("zones must contain at most 32 players")
else
    to_entries
    | map(
        .key as $index
        | .value
        | ("zones[" + ($index | tostring) + "]") as $label
        | if type != "object" then error($label + " must be an object") else . end
        | if (keys - ["id", "name", "output", "device", "channel_pair", "channels", "port", "log_level", "server",
                      "buffer_ms", "hook_start", "hook_stop"] | length) > 0 then
            error($label + " contains an unknown option")
          else . end
        | .id |= nonempty_text($label + ".id")
        | if (.id | test("^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")) then .
          else error($label + ".id must contain 1 to 64 letters, digits, underscores or hyphens and start with a letter or digit") end
        | .name |= nonempty_text($label + ".name")
        | if has("device") == has("output") then
            error($label + " must set exactly one of device or output")
          elif has("device") then
            .device |= nonempty_text($label + ".device")
            | if (.device | test("^/dev/snd/((by-id|by-path)/[^/]+|controlC[0-9]+|pcmC[0-9]+D[0-9]+p)$")) and (.device | test("/\\.{1,2}$") | not) then .
              else error($label + ".device must select a soundcard control or playback device") end
            | if has("channels") and has("channel_pair") then
                error($label + " must choose Output pair or Custom channels, not both")
              elif has("channels") then .channels |= channels_value($label + ".channels")
              elif has("channel_pair") then .channel_pair |= pair_value($label + ".channel_pair")
              else .channel_pair = "front" end
          else
            .output |= nonempty_text($label + ".output")
            | if has("channel_pair") or has("channels") then error($label + " output pairs and custom channels require device") else . end
          end
        | .port = (if has("port") then .port else 8928 + $index end)
        | if (.port | type) != "number" then error($label + ".port must be a number")
          elif .port < 1024 or .port > 65535 or .port != (.port | floor) then
            error($label + ".port must be a whole number between 1024 and 65535")
          else . end
        | if has("buffer_ms") then .buffer_ms |= buffer_value($label + ".buffer_ms") else . end
        | $settings + .
        | .log_level |= log_value($label + ".log_level")
        | .server |= server_value($label + ".server")
        | .hook_start |= text_value($label + ".hook_start")
        | .hook_stop |= text_value($label + ".hook_stop")
        | . + {client_id: .id}
    )
    | if (map(.id) | unique | length) != length then error("zone ids must be unique")
      elif (map(.port) | unique | length) != length then error("zone ports must be unique")
      elif ([.[] | select(has("output")) | .output] | unique | length) != ([.[] | select(has("output"))] | length) then
        (group_by(.output) | map(select(.[0] | has("output")) | select(length > 1)) | .[0]) as $duplicate
        | error($duplicate[0].name + " and " + $duplicate[1].name + " select the same explicit output")
      else . end
end
