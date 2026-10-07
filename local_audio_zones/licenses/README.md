# Player dependency notices

These notices accompany the native player built from the pinned CLI source.
License files are copied unchanged from the commits below. The two source-notice
files retain complete license comment blocks from library sources, including
IXWebSocket's embedded Base64, URL parser, UTF-8 validator and handshake code,
and Opus's CELT, SILK and shared codec sources. Repeated blocks are listed once
with their source paths.

| Project | Upstream ref | Verified commit | License files |
| --- | --- | --- | --- |
| [sendspin-cpp-cli](https://github.com/Sendspin/sendspin-cpp-cli/tree/bc0551c6f0c29ee1a773c4ea730bb20b71992a8a) | v0.3.0 | `bc0551c6f0c29ee1a773c4ea730bb20b71992a8a` | [sendspin-cpp-cli.LICENSE](sendspin-cpp-cli.LICENSE) |
| [sendspin-cpp](https://github.com/Sendspin/sendspin-cpp/tree/9331ace6428979982c934384702d289958ca125e) | v0.8.0 | `9331ace6428979982c934384702d289958ca125e` | [sendspin-cpp.LICENSE](sendspin-cpp.LICENSE) |
| [ArduinoJson](https://github.com/bblanchon/ArduinoJson/tree/32520135092970120a5ac165cf45f48e658c421d) | v7.4.1 | `32520135092970120a5ac165cf45f48e658c421d` | [ArduinoJson.LICENSE](ArduinoJson.LICENSE) |
| [IXWebSocket](https://github.com/machinezone/IXWebSocket/tree/c5a02f1066fb0fde48f80f51178429a27f689a39) | v11.4.5 | `c5a02f1066fb0fde48f80f51178429a27f689a39` | [IXWebSocket.LICENSE](IXWebSocket.LICENSE) |
| [micro-flac](https://github.com/esphome-libs/micro-flac/tree/9f8bfe5c9ee46cea175084b49ae8ac95545705b5) | v0.1.1 | `9f8bfe5c9ee46cea175084b49ae8ac95545705b5` | [micro-flac.LICENSE](micro-flac.LICENSE) |
| [micro-opus](https://github.com/esphome-libs/micro-opus/tree/3e9ce44c56ab8007261d68c511f0162599542aa2) | v0.3.5 | `3e9ce44c56ab8007261d68c511f0162599542aa2` | [micro-opus.LICENSE](micro-opus.LICENSE) |
| [micro-ogg-demuxer](https://github.com/esphome-libs/micro-ogg-demuxer/tree/865ad9d831e7dc76bb9c142607bae33fc75648e7) | v1.2.0 | `865ad9d831e7dc76bb9c142607bae33fc75648e7` | [micro-ogg-demuxer.LICENSE](micro-ogg-demuxer.LICENSE) |
| [opus](https://github.com/xiph/opus/tree/22244de5a79bd1d6d623c32e72bf1954b56235be) | v1.6.1 | `22244de5a79bd1d6d623c32e72bf1954b56235be` | [opus.COPYING](opus.COPYING), [opus.LICENSE_PLEASE_READ.txt](opus.LICENSE_PLEASE_READ.txt) |

The CLI's CMake configuration selects Sendspin core v0.8.0. Its host build
selects ArduinoJson, IXWebSocket, micro-flac and micro-opus at the refs above.
micro-opus and micro-flac share the same micro-ogg-demuxer submodule commit;
micro-opus also builds its Opus submodule with the patches supplied by micro-opus.

Opus's COPYING and LICENSE_PLEASE_READ.txt retain the upstream patent-license
references. These files do not replace or change those grants.

The image installs this directory at
`/usr/share/doc/local-audio-zones/licenses/`. Debian-provided shared libraries
retain their package copyright files under `/usr/share/doc/`.
