"""Configuration store — persists settings to config/config.json."""
import json
from pathlib import Path
from typing import Any, Optional
from src.metadata.schemas import DEFAULT_FIELD_MAPPING
from src.utils.paths import data_path
from src.utils.logging import setup_logging

logger = setup_logging("config")

CONFIG_PATH = data_path("config", "config.json")

DEFAULT_CONFIG = {
    "baserow": {
        "api_url": "https://baserow.jymedia.cc",
        "api_token": "",
        "table_id": 810,
        "field_mapping": dict(DEFAULT_FIELD_MAPPING),
        "field_mapping_verified": False,
        "table_fields_cache": [],
    },
    "scraping": {
        "min_width": 800,
        "min_height": 600,
        "max_pages": 10,
        "page_delay_seconds": 2,
        "max_concurrent_downloads": 3,
        "download_delay_seconds": 1,
        "scroll_count": 5,
        "scroll_wait_ms": 800,
        "allowed_aspects": [],
        "allow_mobile": True,
        "watermark_detection": True,
        "enhance_near_miss": True,
        "max_enhance_upscale": 2.5,
        "allow_nsfw": False,
    },
    "jpeg": {
        "quality": 85,
        "optimize": True,
        "subsampling": 0,
    },
    "scheduler": {
        "enabled": True,
        "check_interval_minutes": 5,
    },
    "gallery": {
        "max_entries": 5000,
        "max_thumbnails": 5000,
    },
    "ai": {
        "enabled": True,
        "cloud_provider": "",
        "cloud_api_key": "",
        "cloud_model": "",
        "cloud_enabled": False,
        # User-editable prompts and word lists (empty = use built-in default)
        "cloud_system_prompt": "",
        "strip_words": "",
        "robotic_adjectives": "",
        "junk_tags": "",
        "generic_prefixes": "",
    },
}


class ConfigStore:
    """Thread-safe config store backed by JSON file."""

    def __init__(self):
        self._config: dict = {}
        self._loaded = False

    def load(self) -> dict:
        """Load config from disk, merging with defaults."""
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r") as f:
                    stored = json.load(f)
                self._config = self._deep_merge(DEFAULT_CONFIG, stored)
            else:
                self._config = dict(DEFAULT_CONFIG)
            self._loaded = True
            logger.info("Configuration loaded")
        except Exception as e:
            logger.warning(f"Failed to load config: {e}. Using defaults.")
            self._config = dict(DEFAULT_CONFIG)
            self._loaded = True
        return self._config

    def save(self) -> bool:
        """Save current config to disk."""
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(self._config, f, indent=2, default=str)
            return True
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False

    def get(self, *keys: str, default: Any = None) -> Any:
        """Get a nested config value. E.g., get('baserow', 'api_url')."""
        if not self._loaded:
            self.load()
        d = self._config
        for key in keys:
            if isinstance(d, dict):
                d = d.get(key)
            else:
                return default
            if d is None:
                return default
        return d

    def set(self, *keys_and_value) -> None:
        """Set a nested config value. E.g., set('baserow', 'api_url', 'http://...')."""
        if not self._loaded:
            self.load()
        *keys, value = keys_and_value
        d = self._config
        for key in keys[:-1]:
            if key not in d or not isinstance(d[key], dict):
                d[key] = {}
            d = d[key]
        d[keys[-1]] = value

    def get_section(self, section: str) -> dict:
        """Get an entire config section."""
        if not self._loaded:
            self.load()
        return self._config.get(section, {})

    def update_section(self, section: str, data: dict) -> None:
        """Update an entire config section."""
        if not self._loaded:
            self.load()
        if section not in self._config:
            self._config[section] = {}
        self._config[section].update(data)

    @property
    def config(self) -> dict:
        if not self._loaded:
            self.load()
        return self._config

    def _deep_merge(self, base: dict, override: dict) -> dict:
        """Deep merge override into base."""
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result


# Singleton
config_store = ConfigStore()
