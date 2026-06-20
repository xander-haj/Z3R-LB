from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PALETTE_ASSIGNMENT = "override_armor_palette"
PALETTE_WORD_COUNT = 75
PALETTE_ROW_LENGTH = 15
PALETTE_ROWS = ["Green Mail", "Blue Mail", "Red Mail", "Bunny", "Burning"]
PALETTE_COLOR_MASK = 0xFFFF
# The asset compiler writes armor palette entries as uint16 values, with SNES color data in the low 15 bits.
PALETTE_STORAGE_MASK = 0x8000


class LinkSpritePaletteError(ValueError):
    """User-facing validation error raised when the Link palette block cannot be handled safely."""


def read_link_sprite_palette(project: Path) -> dict[str, Any]:
    path = sprite_sheets_path(project)
    contents = read_sprite_sheets(path)
    region = find_palette_region(contents.splitlines())
    active_values = parse_palette_assignment(region.active_source)
    commented_values = parse_palette_assignment(region.commented_source) if region.commented_source else None
    active = active_values is not None
    values = active_values if active_values is not None else commented_values

    if values is None:
        raise LinkSpritePaletteError("No editable Link armor palette list was found in assets/sprite_sheets.py.")

    normalized = normalize_palette_values(values)
    return {
        "path": str(path),
        "active": active,
        "values": normalized,
        "rows": [
            {"label": label, "start": index * PALETTE_ROW_LENGTH, "length": PALETTE_ROW_LENGTH}
            for index, label in enumerate(PALETTE_ROWS)
        ],
        "row_length": PALETTE_ROW_LENGTH,
        "word_count": PALETTE_WORD_COUNT,
    }


def write_link_sprite_palette(project: Path, values: list[Any], active: bool) -> dict[str, Any]:
    path = sprite_sheets_path(project)
    contents = read_sprite_sheets(path)
    lines = contents.splitlines()
    region = find_palette_region(lines)
    normalized = normalize_palette_values(values)
    lines[region.start:region.end] = format_palette_block(normalized, active)

    trailing_newline = "\n" if contents.endswith("\n") else ""
    path.write_text("\n".join(lines) + trailing_newline, encoding="utf-8")
    return read_link_sprite_palette(project)


def sprite_sheets_path(project: Path) -> Path:
    return project / "assets" / "sprite_sheets.py"


def read_sprite_sheets(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise LinkSpritePaletteError(f"assets/sprite_sheets.py was not found in {path.parent.parent}.") from error


@dataclass
class PaletteRegion:
    start: int
    end: int
    active_source: str | None
    commented_source: str | None


def find_palette_region(lines: list[str]) -> PaletteRegion:
    for index, line in enumerate(lines):
        if not assignment_line_matches(line):
            continue
        end = palette_block_end(lines, index)
        source = "\n".join(lines[index:end])
        commented_source = commented_assignment_source(source) if line.lstrip().startswith("#") else None
        active_source = source if commented_source is None else None

        if active_source is None:
            active_index = next_active_assignment(lines, end)
            if active_index is not None:
                active_end = palette_block_end(lines, active_index)
                active_source = "\n".join(lines[active_index:active_end])
                end = active_end

        return PaletteRegion(index, end, active_source, commented_source)

    raise LinkSpritePaletteError("assets/sprite_sheets.py does not define override_armor_palette.")


def assignment_line_matches(line: str) -> bool:
    stripped = line.lstrip()
    if stripped.startswith("#"):
        stripped = stripped[1:].lstrip()
    return stripped.startswith(f"{PALETTE_ASSIGNMENT} =")


def next_active_assignment(lines: list[str], start: int) -> int | None:
    for index in range(start, len(lines)):
        stripped = lines[index].lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith(f"{PALETTE_ASSIGNMENT} ="):
            return index
        if stripped and not stripped.startswith("#"):
            return None
    return None


def palette_block_end(lines: list[str], start: int) -> int:
    depth = 0
    saw_open = False
    for index in range(start, len(lines)):
        content = uncomment_assignment_line(lines[index])
        depth += content.count("[")
        if "[" in content:
            saw_open = True
        depth -= content.count("]")
        if saw_open and depth <= 0:
            return index + 1
    raise LinkSpritePaletteError("override_armor_palette list is missing its closing bracket.")


def uncomment_assignment_line(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("#"):
        prefix_len = len(line) - len(stripped)
        return line[:prefix_len] + stripped[1:]
    return line


def commented_assignment_source(source: str) -> str:
    return "\n".join(uncomment_assignment_line(line) for line in source.splitlines())


def parse_palette_assignment(source: str | None) -> list[Any] | None:
    if not source:
        return None

    sanitized = strip_palette_row_comments(source)
    match = re.search(rf"{PALETTE_ASSIGNMENT}\s*=\s*(\[.*\])", sanitized, re.DOTALL)
    if not match:
        return None

    try:
        value = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError) as error:
        raise LinkSpritePaletteError(f"override_armor_palette could not be parsed: {error}") from error

    if not isinstance(value, list):
        raise LinkSpritePaletteError("override_armor_palette must be a flat list of SNES color words.")

    return value


def strip_palette_row_comments(source: str) -> str:
    cleaned: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#" + PALETTE_ASSIGNMENT):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def normalize_palette_values(values: list[Any]) -> list[int]:
    if not isinstance(values, list):
        raise LinkSpritePaletteError("Link armor palette must be submitted as a flat list.")

    if len(values) != PALETTE_WORD_COUNT:
        raise LinkSpritePaletteError(f"Link armor palette must contain exactly {PALETTE_WORD_COUNT} colors.")

    normalized: list[int] = []
    for index, value in enumerate(values):
        normalized.append(normalize_palette_word(value, index))
    return normalized


def normalize_palette_word(value: Any, index: int) -> int:
    if isinstance(value, bool):
        raise LinkSpritePaletteError(f"Palette color {index} must be an integer color word, not a boolean.")
    if isinstance(value, int):
        word = value
    elif isinstance(value, str):
        word = parse_palette_word_string(value, index)
    else:
        raise LinkSpritePaletteError(f"Palette color {index} must be an integer or hexadecimal string.")

    if word < 0 or word > PALETTE_COLOR_MASK:
        raise LinkSpritePaletteError(f"Palette color {index} must be between 0x0000 and 0xFFFF.")
    return word


def parse_palette_word_string(value: str, index: int) -> int:
    text = value.strip().lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{1,4}", text):
        raise LinkSpritePaletteError(f"Palette color {index} must be a 1-4 digit hexadecimal value.")
    return int(text, 16)


def format_palette_block(values: list[int], active: bool) -> list[str]:
    if active:
        return [f"{PALETTE_ASSIGNMENT} = [", *format_palette_rows(values, ""), "]"]

    return [
        f"#{PALETTE_ASSIGNMENT} = [",
        *format_palette_rows(values, "#"),
        "#]",
    ]


def format_palette_rows(values: list[int], prefix: str) -> list[str]:
    lines: list[str] = []
    for index, label in enumerate(PALETTE_ROWS):
        start = index * PALETTE_ROW_LENGTH
        row = values[start:start + PALETTE_ROW_LENGTH]
        lines.append(f"{prefix}  # {label}")
        lines.extend(format_palette_value_lines(row, prefix))
    return lines


def format_palette_value_lines(row: list[int], prefix: str) -> list[str]:
    first = row[:8]
    second = row[8:]
    return [
        f"{prefix}  {format_palette_values(first)},",
        f"{prefix}  {format_palette_values(second)},",
    ]


def format_palette_values(values: list[int]) -> str:
    return ", ".join(f"0x{value:04X}" for value in values)
