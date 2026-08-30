"""prompt — prompt files (system prompt, templates) loaded at runtime."""

from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Return the text of ``prompt/<name>.md`` (cached for the process lifetime)."""
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


__all__ = ["load_prompt"]
