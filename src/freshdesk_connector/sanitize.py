"""Strip characters that can hide instructions or tamper with displays from untrusted text.

Helpdesk text is written by customers and ends up in a model's context and on screens.
Three classes of character are removed:

- Control characters other than newline and tab. These include ESC, which starts ANSI
  terminal escape sequences.
- Format characters (Unicode category Cf): zero-width characters, bidirectional overrides
  used in "Trojan Source" attacks, and tag characters that can carry invisible ASCII
  instructions a model will read but a person cannot see.
- Private-use and unassigned code points, which have no visible meaning.

Visible text, including non-Latin scripts and emoji, is left unchanged.
"""

import unicodedata
from typing import Any

_ALLOWED_CONTROLS = frozenset("\n\t")
_REMOVED_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cn"})


def clean_text(text: str) -> str:
    return "".join(
        char
        for char in text
        if char in _ALLOWED_CONTROLS or unicodedata.category(char) not in _REMOVED_CATEGORIES
    )


def clean_value(value: Any) -> Any:
    """Apply clean_text to every string in a JSON-like value, keys included."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return [clean_value(item) for item in value]
    if isinstance(value, dict):
        return {clean_text(str(k)): clean_value(v) for k, v in value.items()}
    return value
