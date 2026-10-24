import json
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


MODELS = {
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-haiku-5-5": "Claude Haiku 5.5",
    "claude-fable-5-1": "Claude Fable 5.1",
}


def settings_file():
    return state_root() / "settings.json"


def model_name():
    # A model picked in the dashboard wins over LLM_MODEL until it is changed again.
    try:
        chosen = json.loads(settings_file().read_text()).get("model")
    except (OSError, ValueError):
        chosen = None
    return chosen or os.environ.get("LLM_MODEL", "claude-sonnet-5-5")


def set_model(model):
    if model not in MODELS:
        raise ValueError(f"Unknown model {model}")
    file = settings_file()
    try:
        current = json.loads(file.read_text())
    except (OSError, ValueError):
        current = {}
    file.write_text(json.dumps({**current, "model": model}, indent=2))
