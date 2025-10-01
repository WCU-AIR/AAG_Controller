import os
from pathlib import Path

class PromptStore:
    def __init__(self, base_dir=None):
        self.base = Path(base_dir or os.getenv("PROMPTS_DIR", "/app/prompts"))

    def read(self, filename: str) -> str:
        path = self.base / filename
        if not path.is_file():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()
