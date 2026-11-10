"""Cuadrilla's mark. Eight mice, eight inks, one violet."""

from __future__ import annotations

NAME = "Cuadrilla"
TAGLINE = "A crew, not a takeover."
VIOLET = "#7C6AF7"
PILL = "#7C6AF7"

# Stable across sessions. m1 is always Violet, m2 always Amber.
INKS = (
    {"id": "violet", "name": "Violet", "hex": "#7C6AF7"},
    {"id": "amber", "name": "Amber", "hex": "#E2A23B"},
    {"id": "mint", "name": "Mint", "hex": "#3DDC97"},
    {"id": "coral", "name": "Coral", "hex": "#F07167"},
    {"id": "sky", "name": "Sky", "hex": "#5BA8FF"},
    {"id": "gold", "name": "Gold", "hex": "#F2C14E"},
    {"id": "rose", "name": "Rose", "hex": "#E36BAE"},
    {"id": "slate", "name": "Slate", "hex": "#8B93A7"},
)

MARK = """\
  ● ● ●  cuadrilla
  a crew, not a takeover.
"""


def ink_for(mouse: str) -> dict[str, str]:
    digits = "".join(ch for ch in str(mouse) if ch.isdigit())
    index = max(int(digits) - 1, 0) if digits else 0
    return dict(INKS[index % len(INKS)])


def mouse_record(session: str, mouse: str) -> dict[str, object]:
    return {
        "driver_session": f"cuadrilla-{session}-{mouse}",
        "started": False,
        "ink": ink_for(mouse),
    }
