# prompt_store.py
import os
from pathlib import Path

class PromptStore:
    def __init__(self, base_dir: str | None = None, default_name: str | None = None):
        # Prefer explicit arg, else env PROMPTS_DIR, else $HOME/prompts fallback
        home_prompts = Path(os.getenv("HOME") or ".") / "prompts"
        base = base_dir or os.getenv("PROMPTS_DIR") or str(home_prompts)
        self.base = Path(base)
        self.default_name = default_name or os.getenv("PROMPT_DEFAULT", "system_default.md")

    def read(self, filename: str | None = None) -> str:
        name = filename or self.default_name
        path = self.base / name
        if not path.is_file():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()
