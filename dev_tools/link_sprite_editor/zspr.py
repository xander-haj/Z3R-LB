from __future__ import annotations

from z3r_launcher.errors import LauncherError


def parse_zspr_preview(data: bytes) -> tuple[bytes, bytes]:
    if len(data) < 21 or data[0:4] != b"ZSPR":
        raise LauncherError("Selected file is not a valid ZSPR sprite.")
    pixel_offset = read_u32_le(data, 9)
    pixel_length = read_u16_le(data, 13)
    palette_offset = read_u32_le(data, 15)
    palette_length = read_u16_le(data, 19)
    if pixel_length == 0:
        raise LauncherError("Selected ZSPR file does not include pixel data.")
    return (
        read_bounded_slice(data, pixel_offset, min(pixel_length, 0x7000)),
        read_bounded_slice(data, palette_offset, min(palette_length, 256)),
    )


def read_u16_le(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise LauncherError("Selected ZSPR file has a truncated header.")
    return int.from_bytes(data[offset:offset + 2], "little")


def read_u32_le(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise LauncherError("Selected ZSPR file has a truncated header.")
    return int.from_bytes(data[offset:offset + 4], "little")


def read_bounded_slice(data: bytes, offset: int, length: int) -> bytes:
    end = offset + length
    if end > len(data):
        raise LauncherError("Selected ZSPR file points outside its data.")
    return data[offset:end]
