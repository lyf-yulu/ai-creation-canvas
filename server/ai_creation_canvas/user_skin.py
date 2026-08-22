"""Per-user skin palettes: bounded color overrides for the dark canvas theme."""

from __future__ import annotations

import json
import re
from typing import Mapping

SKIN_VERSION = 1

# Editable tokens; every value must be a #rrggbb color.
SKIN_TOKENS = (
    "bg", "panel", "panel_hover", "border", "border_strong",
    "text", "text_2", "text_3",
    "accent", "accent_soft", "accent_fg", "canvas",
)

# Default dark palette (matches the shipped reference theme).
DEFAULT_SKIN: Mapping[str, str] = {
    "bg": "#1a1a1e",
    "panel": "#232327",
    "panel_hover": "#2e2e33",
    "border": "#333338",
    "border_strong": "#46464d",
    "text": "#e4e4e7",
    "text_2": "#c4c4cc",
    "text_3": "#8b8b94",
    "accent": "#3b82f6",
    "accent_soft": "#60a5fa",
    "accent_fg": "#ffffff",
    "canvas": "#1a1a1e",
}

_MONOCHROME_SKIN: Mapping[str, str] = {
    "bg": "#0c0c0d",
    "panel": "#151517",
    "panel_hover": "#1f1f22",
    "border": "#2e2e33",
    "border_strong": "#404047",
    "text": "#e4e4e7",
    "text_2": "#c4c4cc",
    "text_3": "#8b8b94",
    "accent": "#f4f4f5",
    "accent_soft": "#d4d4d8",
    "accent_fg": "#0c0c0d",
    "canvas": "#0c0c0d",
}

_GREEN_SKIN: Mapping[str, str] = {
    "bg": "#050806",
    "panel": "#0a140e",
    "panel_hover": "#102719",
    "border": "#285038",
    "border_strong": "#3a7650",
    "text": "#dceee1",
    "text_2": "#b9d0c0",
    "text_3": "#86a991",
    "accent": "#58ed87",
    "accent_soft": "#8ff0aa",
    "accent_fg": "#041108",
    "canvas": "#050806",
}

SKIN_PRESETS: Mapping[str, Mapping[str, str]] = {
    "default": DEFAULT_SKIN,
    "monochrome": _MONOCHROME_SKIN,
    "classic-green": _GREEN_SKIN,
}

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
_MAX_SKIN_JSON_BYTES = 8 * 1024


def validate_skin(raw: object) -> dict[str, str]:
    """Return a complete validated palette or raise ValueError."""
    if not isinstance(raw, Mapping):
        raise ValueError("skin must be an object")
    version = raw.get("version")
    colors = raw.get("colors")
    if version != SKIN_VERSION or not isinstance(colors, Mapping):
        raise ValueError("skin format is invalid")
    merged = dict(DEFAULT_SKIN)
    for name, value in colors.items():
        if name not in SKIN_TOKENS or not isinstance(value, str) or _HEX_COLOR.fullmatch(value) is None:
            raise ValueError("skin color is invalid")
        merged[name] = value
    return merged


def encode_skin(colors: Mapping[str, str]) -> str:
    merged = validate_skin({"version": SKIN_VERSION, "colors": dict(colors)})
    return json.dumps({"version": SKIN_VERSION, "colors": merged}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def parse_skin(raw: str | bytes) -> dict[str, str]:
    if isinstance(raw, bytes):
        if len(raw) > _MAX_SKIN_JSON_BYTES:
            raise ValueError("skin is too large")
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or len(raw) > _MAX_SKIN_JSON_BYTES:
        raise ValueError("skin is too large")
    try:
        return validate_skin(json.loads(raw))
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("skin is invalid") from error
