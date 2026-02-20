"""Cloud AI provider for wallpaper captioning via Gemini, Claude, or OpenRouter APIs.

Uses raw HTTP calls (httpx) so no extra SDK dependencies are needed.
Provides title, alt text, and tags that read like a human wrote them —
not robotic AI descriptions.
"""
import base64
import json
import re
import time
from pathlib import Path
from typing import Optional, Tuple

import httpx
from src.utils.logging import setup_logging

logger = setup_logging("ai-provider")

# System prompt shared by all cloud providers.  Written to produce accurate,
# human-sounding titles, alt text, and tags for wallpapers.
# Editable from the Settings UI; empty config = use this default.
DEFAULT_SYSTEM_PROMPT = """\
You are an expert image analyst specializing in wallpaper identification and \
cataloguing. You excel at recognizing characters, franchises, landmarks, art \
styles, and visual composition. Your job is to produce accurate, human-sounding \
titles, descriptions, and tags.

Given an image (and optional webpage context), return a JSON object with exactly \
three fields:

1. "title" — An accurate, specific, evocative title (3-8 words). Title Case.
Priority order:
  a) If you recognise a character: use their name + franchise or action. \
Examples: "Gojo Satoru — Jujutsu Kaisen", "Tanjiro's Water Breathing", \
"2B Overlooking the Ruins — NieR", "Spider-Man Swinging Through Manhattan"
  b) If it's a known place or landmark: name it specifically. \
Examples: "Mount Fuji at Dawn", "Tokyo Tower Neon Night", "Yosemite Half Dome"
  c) If it's a scene or landscape: capture the specific setting and mood. \
Examples: "Crimson Horizon at Dusk", "Misty Forest Trail", "Neon Rain in Shibuya"
  d) If it's abstract or artistic: describe the visual concept. \
Examples: "Geometric Fractals in Blue", "Ink Waves", "Chromatic Swirl"
  e) If it's nature or animals: be specific about species and setting. \
Examples: "Red Fox in Winter Snow", "Coral Reef at Twilight"

NEVER use these words in titles: wallpaper, background, image, photo, picture, \
HD, 4K, free, stock, download, stunning, beautiful, amazing, gorgeous, \
breathtaking, incredible, awesome, perfect. No superlatives — describe what you \
see, not how impressed you are.

2. "alt" — One factual sentence (15-25 words) describing what the image shows. \
Be specific: name characters and their franchise, describe appearance, pose, \
setting, dominant colours, lighting, and art style. Focus on what makes this \
image unique and identifiable. Do NOT start with "A wallpaper of", "An image of", \
or "This image shows". Do NOT use subjective adjectives like "beautiful" or \
"stunning".

3. "tags" — 10-20 lowercase comma-separated search tags. Include ALL that apply:
  - Character name (full name if known)
  - Franchise / series / game name
  - Media type: anime, manga, game, movie, tv, comic, photograph, etc.
  - Art style: anime, digital art, 3d render, pixel art, photograph, illustration, \
oil painting, watercolour, ai generated, etc.
  - Dominant colours: red, blue, golden, dark, neon, pastel, monochrome, etc.
  - Mood / atmosphere: dramatic, serene, moody, vibrant, melancholic, action, etc.
  - Setting: urban, forest, ocean, space, classroom, battlefield, rooftop, etc.
  - Visible subjects: sword, mecha, cat, sunset, rain, cherry blossoms, etc.
  - Time of day if visible: dawn, sunset, night, golden hour, blue hour, etc.
  NEVER include: wallpaper, background, hd, 4k, free, stock, download, image, photo.

If webpage context is provided (title, tags from the source page), use it as a \
hint to improve accuracy — it may name the character, series, or subject. But \
always verify against what you actually see in the image; don't blindly copy \
webpage text that may be generic or wrong.

Return ONLY valid JSON. No markdown fences, no explanation, no commentary."""


def _get_system_prompt():
    """Return the user-configured system prompt, or the built-in default."""
    from src.storage.config_store import config_store
    custom = config_store.get("ai", "cloud_system_prompt", default="")
    return custom if custom and custom.strip() else DEFAULT_SYSTEM_PROMPT


class CloudAIProvider:
    """Stateless helper for calling Gemini or Claude vision APIs."""

    # --- Gemini (Google AI Studio) -------------------------------------------

    @staticmethod
    async def test_gemini(api_key: str) -> dict:
        """Verify a Gemini API key works. Returns {"ok": bool, "error": str?}."""
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models"
            f"?key={api_key}"
        )
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    body = resp.json()
                    models = [
                        m["name"] for m in body.get("models", [])
                        if "gemini" in m.get("name", "").lower()
                    ]
                    return {
                        "ok": True,
                        "models": models[:5],
                        "message": f"Connected — {len(models)} Gemini models available",
                    }
                else:
                    detail = resp.text[:200]
                    return {"ok": False, "error": f"HTTP {resp.status_code}: {detail}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    async def caption_gemini(
        api_key: str,
        image_path: Path,
        model: str = "gemini-2.0-flash",
        scraped_title: str = "",
        scraped_tags: str = "",
    ) -> Tuple[str, str, str]:
        """Call Gemini vision API. Returns (title, alt, tags)."""
        image_bytes = image_path.read_bytes()
        mime = _guess_mime(image_path)
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")

        # Build user message with optional scraped context
        user_parts = []
        if scraped_title or scraped_tags:
            ctx = "Context from the webpage:"
            if scraped_title:
                ctx += f" Title: {scraped_title}."
            if scraped_tags:
                ctx += f" Tags: {scraped_tags}."
            user_parts.append({"text": ctx + "\n\nDescribe this wallpaper image."})
        else:
            user_parts.append({"text": "Describe this wallpaper image."})

        user_parts.append({
            "inline_data": {"mime_type": mime, "data": b64},
        })

        payload = {
            "system_instruction": {"parts": [{"text": _get_system_prompt()}]},
            "contents": [{"parts": user_parts}],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 512,
            },
        }

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
            f":generateContent?key={api_key}"
        )
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()

        # Extract text from response
        text = ""
        for candidate in body.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                text += part.get("text", "")

        return _parse_json_response(text)

    # --- Claude (Anthropic) --------------------------------------------------

    @staticmethod
    async def test_claude(api_key: str) -> dict:
        """Verify a Claude API key works. Returns {"ok": bool, "error": str?}."""
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "Say OK"}],
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    return {
                        "ok": True,
                        "message": "Connected — Claude API key is valid",
                    }
                elif resp.status_code == 401:
                    return {"ok": False, "error": "Invalid API key"}
                else:
                    detail = resp.text[:200]
                    return {"ok": False, "error": f"HTTP {resp.status_code}: {detail}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    async def caption_claude(
        api_key: str,
        image_path: Path,
        model: str = "claude-haiku-4-5-20251001",
        scraped_title: str = "",
        scraped_tags: str = "",
    ) -> Tuple[str, str, str]:
        """Call Claude vision API. Returns (title, alt, tags)."""
        image_bytes = image_path.read_bytes()
        mime = _guess_mime(image_path)
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")

        # Build user content blocks
        content_blocks = []
        if scraped_title or scraped_tags:
            ctx = "Context from the webpage:"
            if scraped_title:
                ctx += f" Title: {scraped_title}."
            if scraped_tags:
                ctx += f" Tags: {scraped_tags}."
            content_blocks.append({"type": "text", "text": ctx})

        content_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": b64,
            },
        })
        content_blocks.append({
            "type": "text",
            "text": "Describe this wallpaper image.",
        })

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 512,
            "system": _get_system_prompt(),
            "messages": [{"role": "user", "content": content_blocks}],
        }

        url = "https://api.anthropic.com/v1/messages"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()

        # Extract text from response
        text = ""
        for block in body.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        return _parse_json_response(text)

    # --- OpenRouter (multi-model gateway) ------------------------------------

    @staticmethod
    async def test_openrouter(api_key: str) -> dict:
        """Verify an OpenRouter API key and list available vision models."""
        try:
            result = await _fetch_openrouter_models(api_key)
            models = result["models"]
            if models:
                return {
                    "ok": True,
                    "message": f"Connected — {len(models)} vision models available",
                }
            else:
                return {"ok": False, "error": "Connected but no vision models found"}
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return {"ok": False, "error": "Invalid API key"}
            detail = e.response.text[:200]
            return {"ok": False, "error": f"HTTP {e.response.status_code}: {detail}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    async def caption_openrouter(
        api_key: str,
        image_path: Path,
        model: str = "",
        scraped_title: str = "",
        scraped_tags: str = "",
    ) -> Tuple[str, str, str]:
        """Call OpenRouter vision API (OpenAI-compatible). Returns (title, alt, tags)."""
        # Auto-select model if not specified
        if not model:
            try:
                result = await _fetch_openrouter_models(api_key)
                model = result.get("recommended", "")
            except Exception:
                pass
        if not model:
            model = "google/gemini-2.0-flash-exp:free"

        image_bytes = image_path.read_bytes()
        mime = _guess_mime(image_path)
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")

        # Build user content (OpenAI vision format)
        user_content = []
        if scraped_title or scraped_tags:
            ctx = "Context from the webpage:"
            if scraped_title:
                ctx += f" Title: {scraped_title}."
            if scraped_tags:
                ctx += f" Tags: {scraped_tags}."
            user_content.append({"type": "text", "text": ctx})

        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
        user_content.append({
            "type": "text",
            "text": "Describe this wallpaper image.",
        })

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/wallpaper-scraper",
            "X-Title": "Wallpaper Scraper",
        }
        payload = {
            "model": model,
            "max_tokens": 512,
            "temperature": 0.4,
            "messages": [
                {"role": "system", "content": _get_system_prompt()},
                {"role": "user", "content": user_content},
            ],
        }

        url = "https://openrouter.ai/api/v1/chat/completions"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()

        # Extract text from OpenAI-format response
        text = ""
        for choice in body.get("choices", []):
            msg = choice.get("message", {})
            text += msg.get("content", "")

        return _parse_json_response(text)

    @staticmethod
    async def get_openrouter_models(api_key: str) -> dict:
        """Return available OpenRouter vision models (cached 10 min)."""
        return await _fetch_openrouter_models(api_key)

    # --- Unified dispatcher --------------------------------------------------

    @staticmethod
    async def caption(
        provider: str,
        api_key: str,
        image_path: Path,
        model: str = "",
        scraped_title: str = "",
        scraped_tags: str = "",
    ) -> Tuple[str, str, str]:
        """Route to the correct provider. Returns (title, alt, tags)."""
        if provider == "gemini":
            return await CloudAIProvider.caption_gemini(
                api_key, image_path,
                model=model or "gemini-2.0-flash",
                scraped_title=scraped_title,
                scraped_tags=scraped_tags,
            )
        elif provider == "claude":
            return await CloudAIProvider.caption_claude(
                api_key, image_path,
                model=model or "claude-haiku-4-5-20251001",
                scraped_title=scraped_title,
                scraped_tags=scraped_tags,
            )
        elif provider == "openrouter":
            return await CloudAIProvider.caption_openrouter(
                api_key, image_path,
                model=model or "",
                scraped_title=scraped_title,
                scraped_tags=scraped_tags,
            )
        else:
            raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    async def test_connection(provider: str, api_key: str) -> dict:
        """Test connectivity for the given provider."""
        if provider == "gemini":
            return await CloudAIProvider.test_gemini(api_key)
        elif provider == "claude":
            return await CloudAIProvider.test_claude(api_key)
        elif provider == "openrouter":
            return await CloudAIProvider.test_openrouter(api_key)
        else:
            return {"ok": False, "error": f"Unknown provider: {provider}"}


# --- Helpers -----------------------------------------------------------------

def _guess_mime(path: Path) -> str:
    """Guess MIME type from file extension."""
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")


def _normalize_tags(raw_tags) -> str:
    """Normalize tags from various formats (string, list, etc.) into comma-separated string."""
    if isinstance(raw_tags, list):
        return ", ".join(str(t).strip() for t in raw_tags if str(t).strip())
    return str(raw_tags).strip()


def _extract_fields(data: dict) -> Tuple[str, str, str]:
    """Extract title, alt, tags from a parsed JSON dict. Handles field name variations."""
    title = str(data.get("title", "")).strip()
    # Some models use "description" or "alt_text" instead of "alt"
    alt = str(data.get("alt", "") or data.get("alt_text", "") or data.get("description", "")).strip()
    tags = _normalize_tags(data.get("tags", ""))
    return title, alt, tags


def _parse_json_response(text: str) -> Tuple[str, str, str]:
    """Extract title, alt, tags from model response (JSON or freeform)."""
    text = text.strip()

    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
        title, alt, tags = _extract_fields(data)
        if title:
            return title, alt, tags
    except (json.JSONDecodeError, AttributeError):
        pass

    # Fallback: try to find JSON object in the text
    json_match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            title, alt, tags = _extract_fields(data)
            if title:
                return title, alt, tags
        except (json.JSONDecodeError, AttributeError):
            pass

    # Last resort: use first line as title
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    title = lines[0][:80] if lines else "Untitled"
    alt = lines[1][:200] if len(lines) > 1 else ""
    return title, alt, ""


# --- OpenRouter model discovery ----------------------------------------------

# In-memory cache for OpenRouter vision models (10-minute TTL).
_openrouter_cache = {
    "key_hash": "",
    "models": [],
    "recommended": "",
    "fetched_at": 0,
}
_OPENROUTER_CACHE_TTL = 600  # seconds

# Providers whose models are prioritised for the auto-recommendation.
_REPUTABLE_PROVIDERS = {
    "google", "meta-llama", "anthropic", "openai",
    "mistralai", "qwen", "meta", "deepseek",
}

# Models known to excel at image analysis, captioning, and visual description.
# These are prioritised when auto-recommending for wallpaper processing.
_IMAGE_PROCESSING_PREFERRED = [
    "google/gemini-2.0-flash-exp:free",
    "google/gemini-2.0-flash-001",
    "google/gemini-2.5-flash-preview",
    "google/gemini-2.5-pro-preview",
    "anthropic/claude-sonnet-4",
    "anthropic/claude-haiku-4",
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "meta-llama/llama-4-maverick",
    "meta-llama/llama-4-scout",
    "qwen/qwen-2.5-vl-72b-instruct",
    "qwen/qwen-2.5-vl-7b-instruct",
]
_IMAGE_PROCESSING_PREFERRED_SET = set(_IMAGE_PROCESSING_PREFERRED)


async def _fetch_openrouter_models(api_key: str) -> dict:
    """Fetch vision-capable models from OpenRouter, with caching.

    Returns {"models": [...], "recommended": "model-id"}.
    Each model: {"id", "name", "cost_per_hour", "prompt_price", "completion_price"}.
    Cost estimate: ~120 images/hr, ~500 prompt tokens + ~200 completion tokens each.
    """
    global _openrouter_cache

    key_hash = hash(api_key)
    now = time.time()

    # Return cache if fresh and same key
    if (
        _openrouter_cache["key_hash"] == key_hash
        and _openrouter_cache["models"]
        and now - _openrouter_cache["fetched_at"] < _OPENROUTER_CACHE_TTL
    ):
        return {
            "models": _openrouter_cache["models"],
            "recommended": _openrouter_cache["recommended"],
        }

    # Fetch from OpenRouter
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/wallpaper-scraper",
        "X-Title": "Wallpaper Scraper",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://openrouter.ai/api/v1/models",
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    # Filter to vision-capable models
    vision_models = []
    for m in data.get("data", []):
        # Check architecture.input_modalities first, fall back to top-level
        modalities = (
            m.get("architecture", {}).get("input_modalities", [])
            or m.get("input_modalities", [])
        )
        if "image" not in modalities:
            continue

        pricing = m.get("pricing", {})
        prompt_price = float(pricing.get("prompt", "0") or "0")
        completion_price = float(pricing.get("completion", "0") or "0")

        # Cost estimate: 120 images/hr, ~500 prompt + ~200 completion tokens each
        cost_per_hour = 120 * (500 * prompt_price + 200 * completion_price)

        vision_models.append({
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "cost_per_hour": round(cost_per_hour, 4),
            "prompt_price": pricing.get("prompt", "0"),
            "completion_price": pricing.get("completion", "0"),
            "image_optimized": m["id"] in _IMAGE_PROCESSING_PREFERRED_SET,
        })

    # Sort by cost ascending (free models first)
    vision_models.sort(key=lambda x: x["cost_per_hour"])

    # Auto-recommend: prefer a known image-processing model (free or cheap),
    # then fall back to cheapest reputable-provider model.
    recommended = ""

    # 1. Check preferred image-processing models first (order matters)
    model_ids = {m["id"] for m in vision_models}
    for preferred_id in _IMAGE_PROCESSING_PREFERRED:
        if preferred_id in model_ids:
            recommended = preferred_id
            break

    # 2. Fall back to cheapest reputable-provider model
    if not recommended:
        for m in vision_models:
            provider_slug = m["id"].split("/")[0] if "/" in m["id"] else ""
            if provider_slug in _REPUTABLE_PROVIDERS:
                recommended = m["id"]
                break
    if not recommended and vision_models:
        recommended = vision_models[0]["id"]

    # Update cache
    _openrouter_cache = {
        "key_hash": key_hash,
        "models": vision_models,
        "recommended": recommended,
        "fetched_at": now,
    }

    logger.info(f"Fetched {len(vision_models)} vision models from OpenRouter (recommended: {recommended})")
    return {"models": vision_models, "recommended": recommended}


# Singleton (stateless, just a namespace)
cloud_ai = CloudAIProvider()
