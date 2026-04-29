from __future__ import annotations

import re

# Map our token names (lowercase) to tmux send-keys names.
_TOKEN_MAP: dict[str, str] = {
    "enter": "Enter",
    "cr": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "bs": "BSpace",
    "bspace": "BSpace",
    "backspace": "BSpace",
    "space": "Space",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pgup": "PageUp",
    "pageup": "PageUp",
    "pgdn": "PageDown",
    "pagedown": "PageDown",
    "del": "DC",
    "delete": "DC",
}
_TOKEN_MAP.update({f"f{i}": f"F{i}" for i in range(1, 13)})

_MODIFIER_RE = re.compile(r"^([CSM])-(.+)$", re.IGNORECASE)


class KeyParseError(ValueError):
    pass


def _resolve(token: str) -> str:
    lower = token.lower()
    if lower in _TOKEN_MAP:
        return _TOKEN_MAP[lower]
    m = _MODIFIER_RE.match(token)
    if m:
        mod = m.group(1).upper()
        rest = m.group(2)
        # Allow nested named keys after modifier (e.g. <C-Enter>); else passthrough literal char.
        rest_lower = rest.lower()
        if rest_lower in _TOKEN_MAP:
            return f"{mod}-{_TOKEN_MAP[rest_lower]}"
        if len(rest) == 1:
            return f"{mod}-{rest}"
    raise KeyParseError(f"Unknown key token: <{token}>")


def parse(text: str) -> list[tuple[str, str]]:
    """Split text into ('text', literal) and ('key', tmux_name) segments.

    `<<` escapes a literal `<`. `<Name>` is a named key.
    """
    segments: list[tuple[str, str]] = []
    buffer: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "<":
            if i + 1 < n and text[i + 1] == "<":
                buffer.append("<")
                i += 2
                continue
            end = text.find(">", i + 1)
            if end == -1:
                raise KeyParseError(f"Unterminated `<` at position {i}")
            token = text[i + 1:end]
            if not token:
                raise KeyParseError(f"Empty token `<>` at position {i}")
            tmux_name = _resolve(token)
            if buffer:
                segments.append(("text", "".join(buffer)))
                buffer = []
            segments.append(("key", tmux_name))
            i = end + 1
        else:
            buffer.append(c)
            i += 1
    if buffer:
        segments.append(("text", "".join(buffer)))
    return segments
