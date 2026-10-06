"""Documented wire formats for supported USB serial relay protocols."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Protocol:
    channels: int
    encode: Callable[[int, bool], bytes]
    baudrate: int = 9600
    handshake: bytes = b""
    acknowledgement: bytes = b""
    error: bytes = b""


def lcus(channel, enabled):
    frame = (0xA0, channel, int(enabled))
    return bytes((*frame, sum(frame) & 0xFF))


PROTOCOLS = {
    "DSD TECH SH-UR01A": Protocol(
        channels=1,
        encode=lambda channel, enabled: f"AT+CH{channel}={int(enabled)}\r\n".encode("ascii"),
        handshake=b"AT\r\n", acknowledgement=b"OK", error=b"ERROR",
    ),
    "KMtronic": Protocol(
        channels=1, encode=lambda channel, enabled: bytes((0xFF, channel, int(enabled))),
    ),
    "LCUS": Protocol(channels=8, encode=lcus),
}


def command(protocol, channel, enabled):
    driver = PROTOCOLS.get(protocol)
    if driver is None or type(channel) is not int or not 1 <= channel <= driver.channels:
        raise ValueError("Invalid relay protocol or channel")
    return driver.encode(channel, enabled)
