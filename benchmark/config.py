"""Central configuration. Single source of truth for env vars and LLM settings.

All values live in ``configs.json`` (same folder). On import, entries under
``"env"`` are exported to ``os.environ`` (non-empty values win) so the whole
project reads configuration from one file instead of scattered ``os.getenv``
calls or CLI flags.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("configs.json")

_DEFAULTS: dict = {
    "env": {
        "OPENAI_API_KEY": "",
        "LLM_API_KEY": "",
        "OLLAMA_HOST": "http://185.239.2.48:11434",
    },
    "llm": {
        "provider": "ollama",
        "endpoint": "http://185.239.2.48:11434",
        "model": "qwen2.5-coder:7b",
        "timeout": 30.0,
        "fallback_to_lcs": True,
        "cache_enabled": True,
    },
}

_cache: dict | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def load_configs(path: str | Path = CONFIG_PATH, *, apply_env: bool = True) -> dict:
    """Read configs.json, merge over defaults, optionally export env vars."""
    global _cache
    raw: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
    except FileNotFoundError:
        raw = {}
    merged = _deep_merge(_DEFAULTS, raw if isinstance(raw, dict) else {})
    # endpoint fallback: explicit llm.endpoint wins, else LLM_ENDPOINT/OLLAMA_HOST env
    llm = merged.get("llm", {})
    if not llm.get("endpoint"):
        llm["endpoint"] = os.getenv(
            "LLM_ENDPOINT", os.getenv("OLLAMA_HOST", "http://localhost:11434")
        )
    merged["llm"] = llm
    if apply_env:
        for k, v in merged.get("env", {}).items():
            if v not in (None, ""):
                os.environ[str(k)] = str(v)
    _cache = merged
    return merged


def _ensure_loaded() -> dict:
    global _cache
    if _cache is None:
        return load_configs()
    return _cache


def reload() -> dict:
    """Re-read configs.json from disk (picks up external edits)."""
    return load_configs(apply_env=True)


def save(configs: dict, path: str | Path = CONFIG_PATH) -> dict:
    """Persist a full configs dict to disk and re-apply env vars."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(configs, f, indent=2)
    return load_configs(path)


def get_env(name: str, default: str | None = None) -> str | None:
    cfg = _ensure_loaded()
    if name in cfg.get("env", {}) and cfg["env"][name] not in (None, ""):
        return str(cfg["env"][name])
    return os.getenv(name, default)


def get_llm_settings() -> dict:
    """Return the ``llm`` section of configs.json (copy)."""
    return dict(_ensure_loaded().get("llm", {}))


def get_llm_config():
    """Build an :class:`llm_matcher.LLMConfig` from configs.json (lazy import)."""
    from .llm_matcher import LLMConfig

    return LLMConfig.from_config()


# Load once at import so `import app.config` sets up os.environ globally.
load_configs()
