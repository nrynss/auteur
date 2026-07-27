"""Shared word-boundary matcher + keyword tables.

One matcher for both spike scoring (scratch/run_spike.py) and T1 routing —
never fork these into two copies that drift.
"""

import re


def has_word(text: str, word: str) -> bool:
    """Prefix match anchored at a left word boundary: 'wood' matches 'wooden',
    'light' matches 'lighting' but not 'sunlight'."""
    return re.search(rf"\b{re.escape(word)}", text) is not None


MATERIALS = ["gravel", "wood", "glass", "snow", "metal"]

# trigger words (looked for in ground truth) -> accepted words (required in prediction).
# Materials are checked separately and deliberately excluded here so that a
# material word alone can never satisfy an action check.
ACTIONS = {
    "footsteps": (["footstep", "walking"], ["footstep", "walk", "step", "tread", "pace", "crunch", "boot"]),
    "strike":    (["striking", "strike"], ["strike", "striking", "ignite", "light", "spark"]),
    "creak":     (["creak", "squeak"], ["creak", "squeak", "squeal", "groan"]),
    "close":     (["closing", "shut"], ["clos", "shut", "slam", "thud"]),
    "open":      (["opening"], ["open", "unlatch", "unfasten"]),
    "break":     (["breaking", "shatter"], ["break", "shatter", "smash", "crash"]),
    "rock":      (["rocking", "wobbl", "rolling"], ["rock", "wobbl", "roll", "rattl", "clink", "stopping", "tipping"]),
}
