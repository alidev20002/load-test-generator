"""Unified application configuration module with flat settings dictionary and direct attribute access."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "configs.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "llm_provider": "openai",
    "llm_endpoint": "http://185.239.2.48:11434/",
    "llm_apikey": "",
    "model": "gemma3:4b",
    "timeout": 30.0,
    "fallback_to_lcs": True,
    "cache_enabled": True,
}

CONFIGS: dict[str, Any] = dict(DEFAULT_CONFIG)

# Direct module-level configuration attributes
llm_provider: str = DEFAULT_CONFIG["llm_provider"]
llm_endpoint: str = DEFAULT_CONFIG["llm_endpoint"]
llm_apikey: str = DEFAULT_CONFIG["llm_apikey"]
model: str = DEFAULT_CONFIG["model"]
timeout: float = float(DEFAULT_CONFIG["timeout"])
fallback_to_lcs: bool = bool(DEFAULT_CONFIG["fallback_to_lcs"])
cache_enabled: bool = bool(DEFAULT_CONFIG["cache_enabled"])

# Common aliases for direct imports
openai_apikey: str = llm_apikey
open_ai_apikey: str = llm_apikey
opeai_apikey: str = llm_apikey


def _sync_module_attributes() -> None:
    """Synchronize global module attributes and environment variables with CONFIGS dictionary."""
    global llm_provider, llm_endpoint, llm_apikey, model, timeout, fallback_to_lcs, cache_enabled
    global openai_apikey, open_ai_apikey, opeai_apikey

    llm_provider = str(CONFIGS.get("llm_provider", DEFAULT_CONFIG["llm_provider"]))
    llm_endpoint = str(CONFIGS.get("llm_endpoint", DEFAULT_CONFIG["llm_endpoint"]))
    llm_apikey = str(CONFIGS.get("llm_apikey", DEFAULT_CONFIG["llm_apikey"]))
    model = str(CONFIGS.get("model", DEFAULT_CONFIG["model"]))
    timeout = float(CONFIGS.get("timeout", DEFAULT_CONFIG["timeout"]))
    fallback_to_lcs = bool(CONFIGS.get("fallback_to_lcs", DEFAULT_CONFIG["fallback_to_lcs"]))
    cache_enabled = bool(CONFIGS.get("cache_enabled", DEFAULT_CONFIG["cache_enabled"]))

    openai_apikey = llm_apikey
    open_ai_apikey = llm_apikey
    opeai_apikey = llm_apikey

    # Populate process environment variables from configuration
    os.environ["LLM_PROVIDER"] = llm_provider
    os.environ["LLM_ENDPOINT"] = llm_endpoint
    os.environ["LLM_APIKEY"] = llm_apikey
    os.environ["OPENAI_API_KEY"] = llm_apikey
    os.environ["OLLAMA_HOST"] = llm_endpoint
    os.environ["LLM_MODEL"] = model


def load_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    """Read flat JSON configuration from disk, merge over defaults, and update module attributes."""
    global CONFIGS
    target_path = Path(path)
    file_data: dict[str, Any] = {}

    if target_path.is_file():
        try:
            with target_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    file_data = loaded
        except (json.JSONDecodeError, OSError):
            file_data = {}

    CONFIGS = {**DEFAULT_CONFIG, **file_data}
    _sync_module_attributes()
    return CONFIGS


def save_config(new_configs: dict[str, Any], path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    """Persist flat configuration dictionary to disk and update module attributes."""
    global CONFIGS
    target_path = Path(path)
    CONFIGS.update(new_configs)
    _sync_module_attributes()

    with target_path.open("w", encoding="utf-8") as f:
        json.dump(CONFIGS, f, indent=2)

    return CONFIGS


def get_config() -> dict[str, Any]:
    """Return a copy of the active configuration dictionary."""
    return dict(CONFIGS)


# Load settings immediately upon module import
load_config()
