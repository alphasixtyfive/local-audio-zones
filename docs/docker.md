# Docker Compose

Use the same image and zone settings on a Linux Docker host. Full multichannel
routing requires a running host PulseAudio server with the intended channels
already exposed. Docker Desktop is not supported for local sound devices.

## Set up

Download the setup files into a new directory:

```sh
mkdir -p local-audio-zones
cd local-audio-zones
base=https://raw.githubusercontent.com/alphasixtyfive/local-audio-zones/main
curl -fsSL "$base/docker-compose.yml" -o docker-compose.yml
curl -fsSL "$base/.env.example" -o .env
curl -fsSL "$base/options.example.json" -o options.json
curl -fsSL "$base/docs/docker.md" -o README.md
```

1. Set your host's PulseAudio runtime directory and authentication cookie in
   `.env`. The example paths are placeholders for a user with UID 1000.
2. Set zone names, IDs, devices and pairs in `options.json`. Find stable devices
   under `/dev/snd/by-id/`, or use `/dev/snd/by-path/` for identical cards.
3. Start the service:

```sh
docker compose config --quiet
docker compose up -d
docker compose logs -f
```

Keep the host audio session running when its user is logged out. Confirm that
`pactl info` works on the host before starting the container. The usual cookie
path is `~/.config/pulse/cookie`; use the cookie configured by your own server.
It is mounted read-only. Anonymous access is not needed.

The container uses host networking for mDNS and incoming player connections.
Allow mDNS on UDP port 5353 and the configured player ports through your local
firewall. If discovery fails, check the app and host mDNS logs.

The sound directory is bind-mounted with sound-device cgroup access so new
device nodes remain visible after USB reconnection. No privileged container
or static device list is needed. The PulseAudio directory mount also exposes
a replaced socket. If the host replaces that directory or the authentication
cookie, recreate the container to refresh its mounts.

## Configure and check

`options.json` uses the [app's zone settings](https://github.com/alphasixtyfive/local-audio-zones/blob/main/local_audio_zones/DOCS.md).
The example needs a multichannel card exposing front and rear pairs. Change
the second zone to another card, or remove it if you have only one stereo output.
After editing settings, recreate the container so it reads the current file:

```sh
docker compose up -d --force-recreate
```

To see the host's active outputs, card numbers and channel maps:

```sh
pactl --format=json list sinks | jq '.[] | {name, card: .properties."alsa.card", channels: .channel_map}'
```

Check the container connection and players with:

```sh
docker compose exec local-audio-zones pactl info
docker compose exec local-audio-zones sendspin-cli -l
docker compose exec local-audio-zones /usr/bin/container-healthcheck
```

Player state is retained in the named data volume. An unhealthy container is
reported by Docker; the restart policy handles container exits, not health
status. Check logs and physical outputs rather than treating health as proof
of audible sound.

Advanced native outputs use the same `output` setting as the app. The supplied
Compose file is configured for PulseAudio; other backends need their own host
connections and do not provide the app's automatic channel routing.

To build locally, use the existing `local_audio_zones/Dockerfile`; there is no
separate standalone runtime. Build with the matching version and architecture,
then point the Compose service at that image.
