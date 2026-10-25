import json
import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

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
    "gpt-5.5": "GPT-5.5",
}

PROVIDER_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def model_provider(model):
    return "openai" if model == "gpt-5.5" else "anthropic"


def model_efforts(model):
    if model_provider(model) == "openai":
        return ["none", "low", "medium", "high", "xhigh"]
    return ["low", "medium", "high"]


def provider_key(provider):
    name = PROVIDER_KEYS[provider]
    # A newly added local credential is available without restarting active runs.
    local = dotenv_values(ROOT / ".env").get(name) if provider == "openai" and not os.environ.get(name) else None
    return (os.environ.get(name) or local or "").strip()


def provider_configured(provider):
    key = provider_key(provider)
    return bool(key) and not (provider == "openai" and key.startswith("sk-ant-"))


def require_model_key(model):
    provider = model_provider(model)
    if not provider_configured(provider):
        raise ValueError(f"Set a valid {PROVIDER_KEYS[provider]} in .env for {MODELS.get(model, model)}")
    return provider_key(provider)


def settings_file():
    return state_root() / "settings.json"


def model_name():
    # A model picked in the dashboard wins over LLM_MODEL until it is changed again.
    try:
        chosen = json.loads(settings_file().read_text()).get("model")
    except (OSError, ValueError):
        chosen = None
    return chosen or os.environ.get("LLM_MODEL", "claude-sonnet-5-5")


def model_settings(model=None, effort=None):
    """Execution settings for new runs; evaluations snapshot these before starting."""
    model = model or model_name()
    opus = model == "claude-opus-5-5"
    default_effort = "high" if model == "claude-sonnet-5-5" else "medium" if opus or model == "gpt-5.5" else "low"
    effort = effort or default_effort
    if effort not in model_efforts(model):
        raise ValueError(f"Unsupported reasoning effort for {model}: {effort}")
    return {"model": model, "provider": model_provider(model), "effort": effort,
            "speed": "fast" if opus else "standard"}


def set_model(model):
    if model not in MODELS:
        raise ValueError(f"Unknown model {model}")
    file = settings_file()
    try:
        current = json.loads(file.read_text())
    except (OSError, ValueError):
        current = {}
    file.write_text(json.dumps({**current, "model": model}, indent=2))
