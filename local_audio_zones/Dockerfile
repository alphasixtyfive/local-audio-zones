# Modified for Local Audio Zones; upstream attribution is in NOTICE.
# Build from pinned upstream source, then apply the documented routing fix.
# Bump both ARGs together; the SHA check rejects a repointed tag.
ARG SENDSPIN_CLI_REF=v0.2.0
ARG SENDSPIN_CLI_SHA=e980128a9c37229cbc28c764e9f2fd65d1c5edb6

# Supervisor and CI pass BUILD_ARCH; plain Docker builds default to amd64.
ARG BUILD_ARCH=amd64

# debian:trixie-slim, by multi-arch index digest rather than a per-architecture
# one, so that a cross-build resolves this stage to the target's architecture.
# Must stay on the same release as the runtime base: the binary built here is
# linked against that image's libc.
FROM debian@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132 AS build

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG SENDSPIN_CLI_REF
ARG SENDSPIN_CLI_SHA

# Use native ALSA, PulseAudio and PipeWire backends without PortAudio.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        libasound2-dev \
        libavahi-compat-libdnssd-dev \
        libpipewire-0.3-dev \
        libpulse-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --depth 1 --branch "${SENDSPIN_CLI_REF}" \
        https://github.com/Sendspin/sendspin-cpp-cli.git . \
    && cloned="$(git rev-parse --verify HEAD)" \
    && if [ "${cloned}" != "${SENDSPIN_CLI_SHA}" ]; then \
        echo "sendspin-cli ${SENDSPIN_CLI_REF} is ${cloned}, expected ${SENDSPIN_CLI_SHA}" >&2; \
        exit 1; \
    fi

COPY patches/0001-pulse-pin-named-output.patch /tmp/pulse-pin-named-output.patch
RUN git apply --check /tmp/pulse-pin-named-output.patch \
    && git apply /tmp/pulse-pin-named-output.patch

# Upstream silently drops backends with missing dependencies; verify the result.
RUN cmake -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DSENDSPIN_CLI_WITH_MDNS=ON \
        -DSENDSPIN_CLI_WITH_PORTAUDIO=OFF \
        -DSENDSPIN_CLI_WITH_PULSE=ON \
        -DSENDSPIN_CLI_WITH_PIPEWIRE=ON \
        -DSENDSPIN_CLI_BUILD_TESTS=OFF \
        | tee configure.log \
    && grep -qE '^-- sendspin-cli audio backends: null, stdout, alsa, pulse, pipewire$' configure.log \
    && grep -qE '^-- sendspin-cli mDNS: dns_sd \(.*libdns_sd\.so.*\)$' configure.log

RUN cmake --build build -j"$(nproc)"

RUN DESTDIR=/stage cmake --install build --component sendspin-cli

# SENDSPIN_GIT_TAG is the Sendspin/sendspin-cpp tag upstream's CMake pulls in via
# FetchContent, so it is the transitive pin this image inherits.
RUN core_tag="$(sed -n 's/^set(SENDSPIN_GIT_TAG "\([^"]*\)".*/\1/p' CMakeLists.txt)" \
    && test -n "${core_tag}" \
    && printf 'SENDSPIN_CLI_REF=%s\nSENDSPIN_CLI_SHA=%s\nSENDSPIN_GIT_TAG=%s\n' \
        "${SENDSPIN_CLI_REF}" "${SENDSPIN_CLI_SHA}" "${core_tag}" \
        > /build-info.txt \
    && sha256sum /tmp/pulse-pin-named-output.patch >> /build-info.txt


# Debian trixie bases with s6-overlay v3. Keep both digests aligned
# with the CI workflows and the Debian release used by the build stage above.
FROM ghcr.io/home-assistant/amd64-base-debian@sha256:9281ee991c28532ddae10114ac84750f4aca287a496f6e19583f3a750ad5e786 AS base-amd64
FROM ghcr.io/home-assistant/aarch64-base-debian@sha256:acc61c0ab60cbca176ec0fe4d37278be514a4a6babfc1becde951c99d17e92f0 AS base-aarch64

FROM base-${BUILD_ARCH}

ARG APP_VERSION=0.1.0

LABEL \
    org.opencontainers.image.version="${APP_VERSION}" \
    org.opencontainers.image.licenses="Apache-2.0"

# pactl resolves room routes and reports output state without Supervisor access.
# ALSA plugins preserve the upstream default-output path on Home Assistant OS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        avahi-daemon \
        dbus \
        libasound2-plugins \
        libasound2t64 \
        libavahi-compat-libdnssd1 \
        libpipewire-0.3-0t64 \
        libstdc++6 \
        pulseaudio-utils \
        python3 \
    && rm -rf /var/lib/apt/lists/*

# Stop the container if native configuration or startup fails.
ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2

# Keep service shutdown and bounded route cleanup within the Supervisor's grace.
ENV S6_KILL_GRACETIME=2000

COPY --from=build /stage/usr/local/bin/sendspin-cli /usr/bin/sendspin-cli
COPY --from=build /build-info.txt /usr/share/sendspin-cli/BUILD-INFO.txt
COPY LICENSE NOTICE HASSCONNECT-LICENSE /usr/share/doc/local-audio-zones/
COPY licenses/ /usr/share/doc/local-audio-zones/licenses/
COPY rootfs/ /

# No ENTRYPOINT or CMD: the base image's /init runs s6, which starts the
# services in rootfs/etc/s6-overlay.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["/usr/bin/container-healthcheck"]
