import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)


def data_root():
    path = Path(os.environ.get("ARENA_DATA", "data/package")).expanduser()
    return path if path.is_absolute() else ROOT / path


def state_root():
    path = Path(os.environ.get("AEIOU_STATE", "data/local")).expanduser()
    path = path if path.is_absolute() else ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_name():
    return os.environ.get("LLM_MODEL", "claude-sonnet-5-5")
