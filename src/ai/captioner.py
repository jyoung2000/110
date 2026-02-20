"""AI captioner using BLIP + CLIP for title, alt text, and tag generation.

Combines visual AI analysis with scraped website metadata to produce
accurate, human-readable captions that recognize characters, media
franchises, and art styles.  The kind of text a person would write when
sharing a wallpaper — not robotic descriptions.

CLIP provides zero-shot character / celebrity recognition against a
persistent dictionary managed via the Characters tab.  The dictionary
grows automatically: when scraping finds character names in page metadata,
they're verified with CLIP and added as discoveries that the user can
confirm or reject from the UI.
"""
import asyncio
import random
import re
from typing import List, Optional, Tuple
from pathlib import Path
from PIL import Image
from src.utils.logging import setup_logging
from src.storage.config_store import config_store

logger = setup_logging("captioner")

# Words to strip from titles/alt — site names, generic labels, junk.
# A human would never write "free 4K stock wallpaper download" — these
# words are metadata noise, not creative descriptions.
# Editable from the Settings UI; empty config = use these defaults.
DEFAULT_STRIP_WORDS = [
    "wallpaper", "wallpapers", "background", "backgrounds", "desktop",
    "hd", "4k", "8k", "uhd", "fhd", "qhd",
    "1080p", "1440p", "2160p", "2k",
    "free download", "download", "free", "stock photo", "stock",
    "royalty free", "no copyright", "creative commons", "public domain",
    "image", "photo", "picture", "pic", "img", "thumbnail", "preview",
    "high quality", "high resolution", "hi-res", "ultra", "best quality",
    "original", "official", "artwork", "fan art", "fanart", "render",
    "iphone", "android", "mobile", "screen saver", "lock screen",
    "wallhaven", "unsplash", "pexels", "pixabay", "wallpaperscraft",
    "wallpaperflare", "wallpaperaccess", "wallpaperbat", "wallpapercave",
    "wallpaperbetter", "hdwallpapers", "getwallpapers", "peakpx",
    "setaswall", "pixel4k", "4kwallpapers", "uhdpaper", "goodfon",
    "fonwall", "rawpixel", "freepik", "deviantart", "artstation",
    "pinterest", "shutterstock", "istock", "gettyimages",
    "depositphotos", "dreamstime", "adobe stock", "123rf", "alamy",
]


def _compile_word_regex(words):
    """Build a compiled regex that matches any of the given words."""
    escaped = [re.escape(w) for w in words if w.strip()]
    if not escaped:
        return re.compile(r"(?!)")  # matches nothing
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.I)


STRIP_WORDS = _compile_word_regex(DEFAULT_STRIP_WORDS)


def _rebuild_strip_words():
    """Rebuild STRIP_WORDS regex from config (or defaults)."""
    global STRIP_WORDS
    custom = config_store.get("ai", "strip_words", default="")
    if custom and custom.strip():
        words = [w.strip() for w in custom.split("\n") if w.strip()]
    else:
        words = DEFAULT_STRIP_WORDS
    STRIP_WORDS = _compile_word_regex(words)

# Patterns that indicate junk metadata (dimensions, IDs, filenames)
JUNK_PATTERN = re.compile(
    r"^\d+x\d+$|"              # "1920x1080"
    r"^[\w-]{20,}$|"           # Long hash/ID strings
    r"^\d+$|"                  # Pure numbers
    r"^IMG_|^DSC_|^DSCN|"     # Camera filenames
    r"^photo-\d|"              # stock photo IDs
    r"\.jpe?g$|\.png$|\.webp$", # File extensions
    re.I,
)

# Dimensions pattern to strip from metadata
_DIMENSION_RE = re.compile(r"\b\d{3,5}\s*[x×]\s*\d{3,5}\b")

# Common stop words for tag extraction
_STOP_WORDS = frozenset({
    "the", "and", "this", "that", "with", "from", "are", "was",
    "for", "has", "its", "is", "of", "in", "on", "at", "to",
    "an", "a", "be", "been", "being", "by", "but", "or", "not",
    "image", "contains", "scene", "shows", "photo", "picture",
    "very", "can", "will", "just", "into", "over", "some", "also",
    "there", "their", "they", "have", "had", "two", "one", "three",
})

# ---------------------------------------------------------------------------
# Media / character / art-style keyword tables
# ---------------------------------------------------------------------------

# Maps media type → keywords found in scraped metadata or URLs
_MEDIA_TYPE_KEYWORDS = {
    "anime": [
        "anime", "manga", "waifu", "kawaii", "shounen", "shonen",
        "shoujo", "shojo", "isekai", "mecha", "seinen", "josei",
        "chibi", "otaku", "senpai", "sensei", "chan", "kun", "sama",
        "hentai", "ecchi", "naruto", "one piece", "dragon ball",
        "attack on titan", "shingeki", "demon slayer", "kimetsu",
        "jujutsu kaisen", "my hero academia", "boku no hero",
        "fullmetal alchemist", "death note", "sword art online",
        "evangelion", "cowboy bebop", "sailor moon", "bleach",
        "hunter x hunter", "gintama", "one punch man", "tokyo ghoul",
        "fairy tail", "black clover", "chainsaw man", "spy x family",
        "bocchi", "frieren", "dandadan", "re:zero", "konosuba",
        "mushoku tensei", "overlord", "genshin", "honkai",
        "studio ghibli", "ghibli", "miyazaki", "makoto shinkai",
        "violet evergarden", "steins gate", "code geass",
    ],
    "game": [
        "game", "gaming", "gameplay", "rpg", "mmorpg", "fps",
        "playstation", "xbox", "nintendo", "steam", "pc game",
        "final fantasy", "zelda", "mario", "elden ring", "dark souls",
        "bloodborne", "sekiro", "genshin impact", "league of legends",
        "valorant", "overwatch", "minecraft", "fortnite", "apex",
        "cyberpunk", "witcher", "skyrim", "halo", "god of war",
        "horizon", "ghost of tsushima", "persona", "nier",
        "resident evil", "metal gear", "kingdom hearts",
        "monster hunter", "pokemon", "fire emblem", "xenoblade",
        "hollow knight", "celeste", "undertale", "baldur",
        "mass effect", "bioshock", "doom", "half-life",
        "world of warcraft", "destiny", "diablo", "starcraft",
    ],
    "movie": [
        "movie", "film", "cinema", "marvel", "mcu", "avengers",
        "star wars", "batman", "superman", "spider-man", "spiderman",
        "harry potter", "lord of the rings", "lotr", "hobbit",
        "jurassic", "transformers", "pixar", "disney", "dreamworks",
        "john wick", "matrix", "blade runner", "interstellar",
        "inception", "joker", "dune", "oppenheimer", "barbie",
    ],
    "tv": [
        "tv show", "series", "television", "netflix", "hbo",
        "game of thrones", "breaking bad", "stranger things",
        "the witcher", "mandalorian", "house of the dragon",
        "the last of us", "wednesday", "peaky blinders",
        "arcane", "invincible", "the boys",
    ],
    "comic": [
        "comic", "comics", "superhero", "dc comics", "marvel comics",
        "manga panel", "webtoon", "graphic novel",
    ],
}

# Maps art style → keywords
_ART_STYLE_KEYWORDS = {
    "anime": [
        "anime", "manga", "cel shaded", "cel-shaded", "chibi",
        "anime style", "anime art", "light novel", "visual novel",
    ],
    "digital art": [
        "digital art", "digital painting", "cg art", "concept art",
        "artstation", "deviantart", "fan art", "fanart",
    ],
    "3d render": [
        "3d", "render", "cgi", "blender", "unreal engine",
        "octane", "cinema 4d", "c4d", "raytracing",
    ],
    "photograph": [
        "photograph", "photography", "camera", "dslr", "canon",
        "nikon", "sony", "fujifilm", "leica", "lens", "f/",
        "exposure", "bokeh", "macro", "telephoto",
    ],
    "pixel art": [
        "pixel art", "pixel", "8-bit", "8bit", "16-bit", "16bit",
        "retro game", "sprite",
    ],
    "illustration": [
        "illustration", "illustrated", "watercolor", "oil painting",
        "pencil", "sketch", "ink", "pastel", "acrylic",
    ],
    "ai generated": [
        "ai generated", "ai art", "midjourney", "dall-e", "dalle",
        "stable diffusion", "comfyui",
    ],
}

# Common separator patterns in titles: "Character - Series", "X | Y"
_TITLE_SEP_RE = re.compile(r"\s+[-|–—]\s+")
# "X from Y" pattern
_FROM_RE = re.compile(r"^(.+?)\s+from\s+(.+)", re.I)

# ---------------------------------------------------------------------------
# Title distillation — strip robotic BLIP patterns to produce short,
# human-sounding wallpaper titles (2-5 words).
# ---------------------------------------------------------------------------

# Filler phrases BLIP loves to insert — only truly robotic ones.
# Scene-descriptive phrases like "in the background", "in the distance",
# "on top of", "next to", "in front of" are kept — they add spatial context.
_BLIP_FILLER_RE = re.compile(
    r"\b("
    r"can be seen|that is|which is|there is|there are|it is|"
    r"we can see|you can see|we see|one can see|"
    r"appears to be|seems to be|looks like|depicted|"
    r"shown here|seen here|featured here|displayed|"
    r"with a lot of|with some|with many|"
    r"on the (?:left|right|side)|"
    r"in the (?:middle|center|corner)|"
    r"at the (?:top|bottom|left|right|center)"
    r")\b",
    re.I,
)

# -ing verbs that are truly filler in a title (generic posture / existence).
# Scene-descriptive verbs like standing, flying, wielding, fighting,
# floating, climbing, riding etc. are kept — they describe what's happening.
_ING_FILLER_RE = re.compile(
    r"\b(looking|posing|resting|leaning|lying|facing|smiling|hanging)\b",
    re.I,
)

# Robotic AI-sounding adjectives — always strip from titles.
# Atmospheric adjectives (vibrant, colorful, scenic, serene, tranquil,
# idyllic, lush, golden, crimson, misty, etc.) are NOT stripped — they
# describe what you actually see and add genuine mood.
# Editable from the Settings UI; empty config = use these defaults.
DEFAULT_ROBOTIC_ADJECTIVES = [
    "beautiful", "stunning", "amazing", "awesome", "incredible", "gorgeous",
    "wonderful", "perfect", "majestic", "breathtaking", "spectacular",
    "magnificent", "lovely", "picturesque", "nice", "great", "cool", "best",
    "ultra", "highly", "extremely", "very", "impressive", "remarkable",
    "exceptional", "extraordinary", "fantastic", "fabulous", "superb",
    "marvelous", "exquisite", "captivating", "mesmerizing", "enchanting",
    "eye-catching", "premium", "high-quality", "professional", "epic",
    "dope", "sick", "insane", "unreal", "absolute", "truly", "really",
    "simply", "quite", "pretty", "quality",
]

_ROBOTIC_ADJ_RE = _compile_word_regex(DEFAULT_ROBOTIC_ADJECTIVES)


def _rebuild_robotic_adj():
    """Rebuild _ROBOTIC_ADJ_RE regex from config (or defaults)."""
    global _ROBOTIC_ADJ_RE
    custom = config_store.get("ai", "robotic_adjectives", default="")
    if custom and custom.strip():
        words = [w.strip() for w in custom.split("\n") if w.strip()]
    else:
        words = DEFAULT_ROBOTIC_ADJECTIVES
    _ROBOTIC_ADJ_RE = _compile_word_regex(words)


# Junk tags removed from the final tag list.
# Editable from the Settings UI; empty config = use these defaults.
DEFAULT_JUNK_TAGS = [
    "wallpaper", "wallpapers", "background", "backgrounds",
    "desktop", "hd", "4k", "8k", "uhd", "1080p", "2k",
    "free", "stock", "download", "image", "photo", "picture",
    "high quality", "best", "top", "new", "latest",
    "iphone", "android", "mobile", "screen saver",
]


def _get_junk_tags():
    """Return the set of junk tags from config (or defaults)."""
    custom = config_store.get("ai", "junk_tags", default="")
    if custom and custom.strip():
        return {w.strip().lower() for w in custom.split("\n") if w.strip()}
    return set(DEFAULT_JUNK_TAGS)


# Generic prefixes stripped from BLIP output.
# Each entry should end with a space (appended automatically from config).
# Editable from the Settings UI; empty config = use these defaults.
DEFAULT_GENERIC_PREFIXES = [
    "a photo of ", "a photograph of ", "an image of ", "a picture of ",
    "a painting of ", "a view of ", "a close up of ", "a closeup of ",
    "a wallpaper of ", "a stock photo of ", "a screenshot of ",
    "there is ", "this is ", "this shows ", "this depicts ",
    "a drawing of ", "an illustration of ",
    "a digital painting of ", "a render of ", "a rendering of ",
    "an art of ", "a piece of art showing ", "a scene of ",
    "a depiction of ", "a portrait of ", "a shot of ",
    "here we see ", "here is ", "here we have ",
    "the image shows ", "the image depicts ", "the photo shows ",
    "we see ", "this image shows ", "this picture shows ",
    "a high quality ", "a beautiful ", "a stunning ",
    "an amazing ", "a gorgeous ", "a breathtaking ",
    "free ", "a free ", "a stock ",
]


def _get_generic_prefixes():
    """Return the list of generic prefixes from config (or defaults)."""
    custom = config_store.get("ai", "generic_prefixes", default="")
    if custom and custom.strip():
        lines = [line.strip() for line in custom.split("\n") if line.strip()]
        # Ensure each prefix ends with a space for matching
        return [p if p.endswith(" ") else p + " " for p in lines]
    return DEFAULT_GENERIC_PREFIXES


# BLIP conditional prompt templates grouped by media type.
# Config format: one "key: prompt text" per line.
# Editable from the Settings UI; empty config = use these defaults.
DEFAULT_BLIP_PROMPTS = {
    "anime": [
        ("character", "this anime character has"),
        ("scene", "this anime scene shows"),
        ("style", "the art style is"),
    ],
    "game": [
        ("character", "the character is"),
        ("scene", "the game environment shows"),
        ("style", "the art style is"),
    ],
    "movie_tv": [
        ("character", "the character is"),
        ("scene", "the scene shows"),
        ("style", "the visual style is"),
    ],
    "general": [
        ("scene", "the scene shows"),
        ("content", "this is a photo of"),
        ("style", "the art style is"),
    ],
}


def _get_blip_prompts(context_key):
    """Return BLIP prompt tuples for a media context from config (or defaults)."""
    config_key = f"blip_prompts_{context_key}"
    custom = config_store.get("ai", config_key, default="")
    if custom and custom.strip():
        prompts = []
        for line in custom.strip().split("\n"):
            if ":" in line:
                key, text = line.split(":", 1)
                key, text = key.strip(), text.strip()
                if key and text:
                    prompts.append((key, text))
        if prompts:
            return prompts
    return DEFAULT_BLIP_PROMPTS.get(context_key, [])


def reload_ai_config():
    """Recompile regex patterns from current config values.

    Called after settings are saved via the API.
    """
    _rebuild_strip_words()
    _rebuild_robotic_adj()


# Filler words to drop from titles — but NOT articles (a/an/the) since
# longer titles need them for natural flow ("Sunset over the Ocean").
_TITLE_FILLER_RE = re.compile(
    r"\b(some|this|that|much|its|it|are|is|was|has|have|been)\b",
    re.I,
)

# Words that stay lowercase in title case (unless first word)
_TITLE_MINOR_WORDS = frozenset({
    "in", "on", "at", "of", "with", "and", "or", "for", "to", "by",
    "but", "nor", "the", "a", "an",
})


class AICaptioner:
    """Generate captions, alt text, and tags using BLIP + scraped metadata.

    Combines visual AI analysis with scraped website metadata to produce
    accurate titles that recognize characters, media franchises, and
    art styles.
    """

    def __init__(self):
        self._model = None
        self._processor = None
        self._clip_model = None
        self._clip_processor = None
        self._char_labels: List[Tuple[str, str, str]] = []  # (name, franchise, media_type)
        self._char_embeddings = None  # Pre-computed CLIP text embeddings
        self._device = "cpu"
        self._initialized = False

    async def initialize(self) -> bool:
        """Load BLIP + CLIP models. Returns False on failure (non-fatal)."""
        try:
            return await asyncio.get_event_loop().run_in_executor(None, self._load_model)
        except Exception as e:
            logger.warning(f"AI model initialization failed: {e}")
            return False

    def _load_model(self) -> bool:
        """Load BLIP (captioning) and CLIP (character recognition) models."""
        try:
            import torch
            from transformers import BlipProcessor, BlipForConditionalGeneration

            logger.info("Loading BLIP-base model...")
            self._processor = BlipProcessor.from_pretrained(
                "Salesforce/blip-image-captioning-base"
            )
            self._model = BlipForConditionalGeneration.from_pretrained(
                "Salesforce/blip-image-captioning-base"
            ).to(self._device)
            self._model.eval()
            self._initialized = True
            logger.info("BLIP-base model loaded successfully")
        except Exception as e:
            logger.warning(f"Failed to load BLIP model: {e}")
            return False

        # CLIP is optional — captioner works without it, just no character ID
        try:
            from transformers import CLIPProcessor, CLIPModel

            logger.info("Loading CLIP model for character recognition...")
            self._clip_processor = CLIPProcessor.from_pretrained(
                "openai/clip-vit-base-patch32"
            )
            self._clip_model = CLIPModel.from_pretrained(
                "openai/clip-vit-base-patch32"
            ).to(self._device)
            self._clip_model.eval()
            self._precompute_character_embeddings()
            logger.info(
                f"CLIP loaded — {len(self._char_labels)} characters indexed"
            )
        except Exception as e:
            logger.warning(f"CLIP model not available, character recognition disabled: {e}")

        return True

    def _precompute_character_embeddings(self):
        """Pre-compute CLIP text embeddings from the character store.

        Loads all confirmed entries (builtin + user + discovered) plus the
        generic baselines from the hardcoded list.
        """
        import torch
        from src.ai.characters import KNOWN_CHARACTERS
        from src.storage.character_store import character_store

        descriptions = []
        labels = []

        # Confirmed characters from the persistent store
        for name, franchise, media_type, desc in character_store.get_clip_entries():
            labels.append((name, franchise, media_type))
            descriptions.append(desc)

        # Generic baselines (always from hardcoded list, not stored)
        for name, franchise, media_type, desc in KNOWN_CHARACTERS:
            if name.startswith("_generic_"):
                labels.append((name, franchise, media_type))
                descriptions.append(desc)

        if not descriptions:
            logger.warning("No characters to index for CLIP")
            return

        with torch.no_grad():
            text_inputs = self._clip_processor(
                text=descriptions, return_tensors="pt", padding=True, truncation=True,
            ).to(self._device)
            embeddings = self._clip_model.get_text_features(**text_inputs)
            self._char_embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True)

        self._char_labels = labels
        logger.info(f"CLIP index built: {len(labels)} entries")

    def refresh_character_embeddings(self):
        """Rebuild CLIP embeddings after the character dictionary changes.

        Called from the API when users add/edit/delete characters.
        """
        if self._clip_model is not None:
            self._precompute_character_embeddings()

    def describe_image_for_clip(
        self,
        image_path: Path,
        character_name: str = "",
        media_type: str = "",
    ) -> str:
        """Generate a rich visual description of an image for CLIP matching.

        Runs multiple BLIP passes to capture subject, appearance, style,
        and setting — then combines them into a single description that
        CLIP can use for zero-shot matching.

        If *character_name* is provided it's prepended so the description
        reads like "Spider-Man, person in red and blue suit with web
        pattern, dynamic pose, digital art".
        """
        import torch

        if not self._initialized:
            return ""

        img = Image.open(image_path).convert("RGB")
        img_resized = img.resize((384, 384), Image.LANCZOS)
        parts = []

        # Unconditional description (who/what is in the image)
        with torch.no_grad():
            inputs = self._processor(img_resized, return_tensors="pt").to(self._device)
            ids = self._model.generate(
                **inputs, max_new_tokens=40, num_beams=5, early_stopping=True,
            )
            raw = self._processor.decode(ids[0], skip_special_tokens=True).strip()
            desc = self._strip_prefixes(raw)
            if desc:
                parts.append(desc)

        # Appearance prompt
        appearance_prompt = (
            "this anime character has" if media_type == "anime"
            else "the person is wearing"
        )
        with torch.no_grad():
            inputs = self._processor(
                img_resized, appearance_prompt, return_tensors="pt",
            ).to(self._device)
            ids = self._model.generate(
                **inputs, max_new_tokens=30, num_beams=3, early_stopping=True,
            )
            raw = self._processor.decode(ids[0], skip_special_tokens=True).strip()
            if raw.lower().startswith(appearance_prompt.lower()):
                raw = raw[len(appearance_prompt):].strip()
            if raw:
                parts.append(raw)

        # Style prompt
        with torch.no_grad():
            inputs = self._processor(
                img_resized, "the art style is", return_tensors="pt",
            ).to(self._device)
            ids = self._model.generate(
                **inputs, max_new_tokens=15, num_beams=3, early_stopping=True,
            )
            raw = self._processor.decode(ids[0], skip_special_tokens=True).strip()
            if raw.lower().startswith("the art style is"):
                raw = raw[len("the art style is"):].strip()
            if raw:
                parts.append(raw)

        # Combine parts, prepend character name if provided
        description = ", ".join(p for p in parts if p)
        if character_name:
            description = f"{character_name}, {description}"

        return description

    def _identify_characters(
        self, image_path: Path, threshold: float = 0.23, top_k: int = 2,
    ) -> List[Tuple[str, str, str, float]]:
        """Zero-shot character recognition via CLIP.

        Returns list of (name, franchise, media_type, score) for matches
        above *threshold*.  Generic baseline labels (``_generic_*``) are
        filtered out — they only exist to absorb probability mass and
        reduce false positives.
        """
        import torch

        if self._clip_model is None or self._char_embeddings is None:
            return []

        try:
            img = Image.open(image_path).convert("RGB")
            with torch.no_grad():
                image_inputs = self._clip_processor(
                    images=img, return_tensors="pt",
                ).to(self._device)
                image_features = self._clip_model.get_image_features(**image_inputs)
                image_features = image_features / image_features.norm(dim=-1, keepdim=True)

                # Cosine similarity against all character embeddings
                similarities = (image_features @ self._char_embeddings.T).squeeze(0)

            values, indices = similarities.topk(min(top_k + 5, len(self._char_labels)))
            results = []
            for val, idx in zip(values, indices):
                score = val.item()
                if score < threshold:
                    break
                name, franchise, media_type = self._char_labels[idx.item()]
                # Skip generic baseline labels
                if name.startswith("_generic_"):
                    continue
                results.append((name, franchise, media_type, score))
                if len(results) >= top_k:
                    break

            if results:
                names = ", ".join(f"{n} ({s:.2f})" for n, _, _, s in results)
                logger.info(f"CLIP character match: {names}")
            return results
        except Exception as e:
            logger.warning(f"CLIP character recognition failed: {e}")
            return []

    def _auto_discover_characters(self, image_path: Path, context: dict):
        """Try to discover new characters from scraped metadata.

        When the metadata names a character that isn't in our dictionary,
        we generate a CLIP description, verify the image matches, and
        record it as a discovery in the character store.
        """
        import torch
        from src.storage.character_store import character_store

        chars = context.get("characters", [])
        media = context.get("media_name", "")
        media_type = context.get("media_type", "")

        for char_name in chars:
            # Already in the store?
            existing = character_store.search(char_name)
            if any(e["name"].lower() == char_name.lower() for e in existing):
                # Bump discovery count for known entries
                for e in existing:
                    if e["name"].lower() == char_name.lower():
                        character_store.discover(
                            name=e["name"],
                            franchise=e.get("franchise", ""),
                        )
                continue

            # Generate a CLIP description for the new character
            desc_parts = [char_name]
            if media:
                desc_parts.append(f"from {media}")
            if media_type == "anime":
                desc_parts.append("anime character")
            elif media_type == "game":
                desc_parts.append("video game character")
            elif media_type in ("movie", "tv"):
                desc_parts.append("character")
            elif media_type == "comic":
                desc_parts.append("superhero comic character")
            clip_desc = ", ".join(desc_parts)

            # Verify with CLIP: does this image actually match?
            try:
                img = Image.open(image_path).convert("RGB")
                with torch.no_grad():
                    image_inputs = self._clip_processor(
                        images=img, return_tensors="pt",
                    ).to(self._device)
                    image_features = self._clip_model.get_image_features(**image_inputs)
                    image_features = image_features / image_features.norm(dim=-1, keepdim=True)

                    text_inputs = self._clip_processor(
                        text=[clip_desc], return_tensors="pt", padding=True, truncation=True,
                    ).to(self._device)
                    text_features = self._clip_model.get_text_features(**text_inputs)
                    text_features = text_features / text_features.norm(dim=-1, keepdim=True)

                    score = (image_features @ text_features.T).item()

                if score >= 0.20:
                    character_store.discover(
                        name=char_name,
                        franchise=media,
                        media_type=media_type,
                        clip_description=clip_desc,
                    )
                    logger.info(
                        f"Auto-discovered character: {char_name} "
                        f"(score={score:.2f}, franchise={media})"
                    )
            except Exception as e:
                logger.debug(f"CLIP verify failed for {char_name}: {e}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def caption(
        self,
        image_path: Path,
        scraped_title: str = "",
        scraped_alt: str = "",
        scraped_tags: str = "",
        source_url: str = "",
    ) -> Tuple[str, str, str]:
        """Generate title, alt_text, tags for an image.

        Uses scraped metadata for character/media context and BLIP for
        visual description, then merges both into natural captions.

        Returns (title, alt_text, comma_separated_tags).
        """
        # 1. Extract structured context from scraped metadata
        context = self._extract_context(scraped_title, scraped_alt, scraped_tags, source_url)

        # 2. CLIP character recognition — only when metadata lacks a name
        if not context["characters"] and self._clip_model is not None:
            try:
                clip_hits = await asyncio.get_event_loop().run_in_executor(
                    None, self._identify_characters, image_path,
                )
                for name, franchise, media_type, _score in clip_hits:
                    context["characters"].append(name)
                    if franchise and not context["media_name"]:
                        context["media_name"] = franchise
                    if media_type and not context["media_type"]:
                        context["media_type"] = media_type
            except Exception as e:
                logger.warning(f"CLIP recognition failed: {e}")

        # 2b. Auto-discover characters from metadata that aren't in the
        #     dictionary yet.  Uses CLIP to verify the image actually
        #     depicts the named character before recording the discovery.
        if context["characters"] and self._clip_model is not None:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._auto_discover_characters,
                    image_path,
                    context,
                )
            except Exception:
                pass  # discovery is best-effort

        # 3. Try cloud AI provider first (Gemini / Claude) if enabled
        cloud_cfg = config_store.get_section("ai")
        use_cloud = (
            cloud_cfg.get("cloud_enabled")
            and cloud_cfg.get("cloud_provider")
            and cloud_cfg.get("cloud_api_key")
        )

        if use_cloud:
            try:
                from src.ai.provider import cloud_ai
                cloud_title, cloud_alt, cloud_tags = await cloud_ai.caption(
                    provider=cloud_cfg["cloud_provider"],
                    api_key=cloud_cfg["cloud_api_key"],
                    image_path=image_path,
                    model=cloud_cfg.get("cloud_model", ""),
                    scraped_title=scraped_title,
                    scraped_tags=scraped_tags,
                )
                if cloud_title:
                    # Apply the same word filters used by the local pipeline
                    cloud_title, cloud_alt, cloud_tags = self._filter_cloud_result(
                        cloud_title, cloud_alt, cloud_tags,
                    )
                    logger.info(
                        f"Cloud AI ({cloud_cfg['cloud_provider']}) captioned: "
                        f"{cloud_title[:50]}"
                    )
                    return cloud_title, cloud_alt, cloud_tags
            except Exception as e:
                logger.warning(
                    f"Cloud AI captioning failed, falling back to BLIP: {e}"
                )

        # 4. Fallback: Run local BLIP captioning (context-aware prompts)
        ai: Optional[dict] = None
        if self._initialized:
            try:
                ai = await asyncio.get_event_loop().run_in_executor(
                    None, self._generate_captions, image_path, context
                )
            except Exception as e:
                logger.warning(f"Caption generation failed: {e}")

        # 5. Merge AI + scraped context into final outputs
        title = self._build_title(ai, context)
        alt_text = self._build_alt(ai, context)
        tags = self._build_tags(ai, context, image_path)

        return title, alt_text, tags

    # ------------------------------------------------------------------
    # Context extraction from scraped metadata
    # ------------------------------------------------------------------

    def _extract_context(self, title: str, alt: str, tags: str, url: str) -> dict:
        """Parse scraped metadata for characters, media, art style, subjects."""
        all_text = f"{title} {alt} {tags}".lower()
        url_lower = url.lower() if url else ""

        context = {
            "characters": [],       # Detected character names
            "media_name": "",       # Series / franchise name
            "media_type": "",       # anime, game, movie, tv, comic
            "art_style": "",        # anime, digital art, photograph, …
            "subjects": [],         # Key subjects extracted from tags
            "clean_title": "",      # Cleaned version of scraped title
            "clean_tags": [],       # Cleaned individual tag strings
        }

        # Clean the raw title
        if title:
            context["clean_title"] = self._clean_metadata_text(title)

        # Clean individual tags
        if tags:
            context["clean_tags"] = [
                t.strip() for t in tags.split(",")
                if t.strip() and not JUNK_PATTERN.search(t.strip())
            ]

        # --- Detect media type ---
        for mtype, keywords in _MEDIA_TYPE_KEYWORDS.items():
            if any(kw in all_text or kw in url_lower for kw in keywords):
                context["media_type"] = mtype
                break

        # --- Detect art style ---
        for style, keywords in _ART_STYLE_KEYWORDS.items():
            if any(kw in all_text or kw in url_lower for kw in keywords):
                context["art_style"] = style
                break
        # Fallback: if media type is anime but no explicit style detected
        if context["media_type"] == "anime" and not context["art_style"]:
            context["art_style"] = "anime"

        # --- Extract character / series names from title patterns ---
        if title:
            self._extract_names_from_title(title, context)

        # --- Extract subjects from tags ---
        seen_lower = {c.lower() for c in context["characters"]}
        if context["media_name"]:
            seen_lower.add(context["media_name"].lower())
        for tag in context["clean_tags"]:
            tag_clean = self._clean_metadata_text(tag)
            if (
                tag_clean
                and len(tag_clean) > 1
                and tag_clean.lower() not in seen_lower
                and not JUNK_PATTERN.search(tag_clean)
            ):
                context["subjects"].append(tag_clean)
                seen_lower.add(tag_clean.lower())

        return context

    def _extract_names_from_title(self, title: str, context: dict):
        """Try to pull character and series names from scraped title text."""
        cleaned = self._clean_metadata_text(title)
        if not cleaned:
            return

        # Pattern: "Character Name - Series Name"  /  "X | Y"  /  "X – Y"
        parts = _TITLE_SEP_RE.split(cleaned, maxsplit=1)
        if len(parts) == 2:
            char_part = parts[0].strip()
            series_part = parts[1].strip()
            if char_part and not JUNK_PATTERN.search(char_part):
                context["characters"].append(char_part)
            if series_part and not JUNK_PATTERN.search(series_part):
                context["media_name"] = series_part
            return

        # Pattern: "Character from Series"
        m = _FROM_RE.match(cleaned)
        if m:
            char = m.group(1).strip()
            series = m.group(2).strip()
            if char and not JUNK_PATTERN.search(char):
                context["characters"].append(char)
            if series and not JUNK_PATTERN.search(series):
                context["media_name"] = series
            return

        # No separator — if the title looks like a proper noun phrase
        # (multiple capitalised words, not generic), treat it as a subject
        words = cleaned.split()
        cap_words = [w for w in words if w[0:1].isupper()]
        if len(cap_words) >= 2 and len(words) <= 5:
            # Likely a character or subject name
            context["characters"].append(cleaned)

    # ------------------------------------------------------------------
    # BLIP caption generation (context-aware)
    # ------------------------------------------------------------------

    def _generate_captions(self, image_path: Path, context: dict) -> dict:
        """Run BLIP with context-aware prompts. Returns dict of results."""
        import torch

        img = Image.open(image_path).convert("RGB")
        img_resized = img.resize((384, 384), Image.LANCZOS)

        result = {
            "description": "",
            "short": "",
            "subject": "",
            "scene": "",
            "style": "",
            "tags": set(),
            "color_tags": set(),
            "mood_tags": set(),
        }

        # --- Pass 1: natural description (unconditional) ---
        with torch.no_grad():
            inputs = self._processor(img_resized, return_tensors="pt").to(self._device)
            ids = self._model.generate(
                **inputs, max_new_tokens=50, num_beams=5, early_stopping=True,
            )
            result["description"] = self._processor.decode(ids[0], skip_special_tokens=True).strip()

        # --- Pass 2: short title (unconditional, tight token limit) ---
        with torch.no_grad():
            inputs = self._processor(img_resized, return_tensors="pt").to(self._device)
            ids = self._model.generate(
                **inputs, max_new_tokens=10, num_beams=3, early_stopping=True,
            )
            result["short"] = self._processor.decode(ids[0], skip_special_tokens=True).strip()

        # --- Pass 3+: context-aware conditional prompts ---
        prompts = self._prompts_for_context(context)
        for key, prompt_text in prompts:
            with torch.no_grad():
                inputs = self._processor(
                    img_resized, prompt_text, return_tensors="pt"
                ).to(self._device)
                ids = self._model.generate(
                    **inputs, max_new_tokens=30, num_beams=3, early_stopping=True,
                )
                raw = self._processor.decode(ids[0], skip_special_tokens=True).strip()

            # Strip the prompt text that BLIP echoes back in the output
            if raw.lower().startswith(prompt_text.lower()):
                raw = raw[len(prompt_text):].strip()

            # Store specific fields
            if key == "style" and not result["style"]:
                result["style"] = raw
            elif key == "subject" and not result["subject"]:
                result["subject"] = raw
            elif key == "scene" and not result["scene"]:
                result["scene"] = raw

            # Harvest tag words from every prompt result
            self._harvest_tags(raw, result["tags"])

        # --- Colour and mood tags ---
        result["color_tags"] = self._extract_color_tags(img)
        result["mood_tags"] = self._mood_from_colors(result["color_tags"])

        return result

    def _prompts_for_context(self, context: dict) -> list:
        """Choose BLIP conditional prompts based on detected context."""
        prompts = [
            ("subject", "the main subject is"),
        ]

        media = context.get("media_type", "")
        style = context.get("art_style", "")

        if media == "anime" or style == "anime":
            prompts.extend(_get_blip_prompts("anime"))
        elif media == "game":
            prompts.extend(_get_blip_prompts("game"))
        elif media in ("movie", "tv", "comic"):
            prompts.extend(_get_blip_prompts("movie_tv"))
        else:
            prompts.extend(_get_blip_prompts("general"))

        return prompts

    @staticmethod
    def _harvest_tags(raw_text: str, tag_set: set):
        """Extract useful tag words from a BLIP output string."""
        for word in raw_text.lower().split():
            clean = word.strip(".,!?;:'\"()[]")
            if len(clean) > 2 and clean not in _STOP_WORDS:
                tag_set.add(clean)

    # ------------------------------------------------------------------
    # Smart merging: AI + scraped context → final title / alt / tags
    # ------------------------------------------------------------------

    def _build_title(self, ai: Optional[dict], context: dict) -> str:
        """Build a creative, human-sounding title (4-10 words).

        A person sharing a wallpaper writes something evocative — they name
        characters, describe the vibe, pick an interesting angle.  They'd
        never write "Free HD Sunset Stock Photo" — they'd write something
        like "Golden Hour over the Pacific" or "Gojo's Hollow Purple".

        Priority:
        1. Character + scene description  →  "Gojo Satoru Unleashing Infinity in the Rain"
        2. Character + media + scene hint →  "Sakura under Cherry Blossoms — Naruto"
        3. Character + media name         →  "Sakura Haruno — Naruto Shippuden"
        4. Character + scene (no franchise)
        5. Clean scraped title (3-10 words after stripping junk)
        6. AI subject + scene combined
        7. AI short caption humanized
        8. Long scraped title distilled
        9. Empty (let engine fallback handle it)
        """
        chars = context.get("characters", [])
        media = context.get("media_name", "")
        art_style = context.get("art_style", "")
        scene_desc = ""

        # Extract a usable scene snippet from AI
        if ai:
            raw_scene = ai.get("scene", "") or ai.get("description", "")
            if raw_scene:
                scene_desc = self._strip_prefixes(raw_scene)
                scene_desc = _ROBOTIC_ADJ_RE.sub(" ", scene_desc)
                scene_desc = _BLIP_FILLER_RE.sub(" ", scene_desc)
                scene_desc = STRIP_WORDS.sub(" ", scene_desc)
                scene_desc = re.sub(r"\s+", " ", scene_desc).strip()
                scene_desc = self._strip_trailing_danglers(scene_desc)

        # --- Path 1: Character + scene description available ---
        if chars and scene_desc:
            char_name = chars[0]
            scene_clean = scene_desc
            if char_name.lower() in scene_clean.lower()[:40]:
                title = self._humanize_title(scene_clean)
            else:
                # Combine: character name woven into the scene
                hint = self._scene_hint(scene_clean)
                if hint and len(hint.split()) >= 2:
                    # Evocative: "Gojo Satoru in the Rain"
                    title = self._humanize_title(f"{char_name} {hint}")
                else:
                    title = self._humanize_title(f"{char_name} {scene_clean}")
            if title and len(title.split()) >= 3:
                if media and media.lower() not in title.lower():
                    candidate = f"{title} — {media}"
                    if len(candidate.split()) <= 10:
                        return self._cap(candidate)
                return self._cap(title)

        # --- Path 2: Character + media, append short scene hint ---
        if chars and media:
            base = f"{chars[0]} — {media}"
            if scene_desc:
                hint = self._scene_hint(scene_desc)
                if hint:
                    base = f"{chars[0]} {hint} — {media}"
            return self._cap(base)

        # --- Path 3: Character name alone with scene ---
        if chars:
            if scene_desc:
                hint = self._scene_hint(scene_desc)
                if hint and len(hint.split()) >= 2:
                    title = self._humanize_title(f"{chars[0]} {hint}")
                else:
                    title = self._humanize_title(f"{chars[0]} {scene_desc}")
                if title:
                    return self._cap(title)
            return self._cap(chars[0])

        # --- Path 4: Clean scraped title (already descriptive enough) ---
        clean_title = context.get("clean_title", "")
        if clean_title:
            trimmed = self._humanize_title(clean_title)
            if trimmed and 3 <= len(trimmed.split()) <= 10:
                return self._cap(trimmed)

        # --- Path 5: AI subject + scene combined ---
        if ai and ai.get("subject"):
            combined = ai["subject"]
            if scene_desc and scene_desc.lower() not in combined.lower():
                # Keep it tight — just the subject + a scene hint
                hint = self._scene_hint(scene_desc)
                if hint:
                    combined = f"{combined} {hint}"
                else:
                    combined = f"{combined} {scene_desc}"
            title = self._humanize_title(combined)
            if title:
                # If we know the art style and it's not a photo, weave it in
                if art_style and art_style not in ("photograph",):
                    style_short = art_style.replace("_", " ")
                    if style_short.lower() not in title.lower():
                        candidate = f"{title} — {style_short}"
                        if len(candidate.split()) <= 10:
                            return self._cap(candidate)
                return self._cap(title)

        # --- Path 6: AI short caption ---
        if ai and ai.get("short"):
            title = self._humanize_title(ai["short"])
            if title:
                return title

        # --- Path 7: Long scraped title as last resort ---
        if clean_title:
            return self._humanize_title(clean_title)

        return ""

    def _scene_hint(self, scene_desc: str) -> str:
        """Extract a short (2-4 word) scene hint from a description.

        Used to enrich character+media titles:
          "standing on a cliff at sunset"  →  "on a Cliff at Sunset"
          "with a glowing sword in darkness"  →  "with Glowing Sword"
          "girl in a school uniform"  →  "in School Uniform"

        Tries to find the most meaningful prepositional phrase (starting
        with on/in/at/under/over/with/near) since those describe setting
        or equipment.  Falls back to the first 3 content words.
        """
        text = self._strip_prefixes(scene_desc)
        text = _ROBOTIC_ADJ_RE.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return ""

        # Try to find a prepositional phrase (most descriptive part)
        m = re.search(
            r"\b(in|on|at|under|over|with|near|against|through|during)\s+.+",
            text, re.I,
        )
        if m:
            phrase = m.group(0).strip()
            phrase = self._strip_trailing_danglers(phrase)
            phrase_words = phrase.split()
            if 2 <= len(phrase_words) <= 5:
                return phrase
            if len(phrase_words) > 5:
                # Trim to 4 words and clean trailing danglers
                trimmed = " ".join(phrase_words[:4])
                trimmed = self._strip_trailing_danglers(trimmed)
                if len(trimmed.split()) >= 2:
                    return trimmed

        # Fallback: first 3 content words, skip leading articles
        words = text.split()
        hint = " ".join(words[:3])
        hint = re.sub(r"^(a|an|the)\s+", "", hint, flags=re.I)
        return hint if len(hint.split()) >= 2 else ""

    def _build_alt(self, ai: Optional[dict], context: dict) -> str:
        """Build descriptive alt text merging AI description + context.

        Alt text should describe what the image actually shows, enriched
        with character/media names when available.  Aims for 15-25 words
        of genuine scene description — no filler adjectives.
        """
        parts = []

        # Start with character/media context if available
        chars = context.get("characters", [])
        media = context.get("media_name", "")
        style = context.get("art_style", "")

        if chars:
            char_label = chars[0]
            if media:
                char_label = f"{chars[0]} from {media}"
            parts.append(char_label)

        # Add AI scene description — prefer scene prompt (richer), fall back to description
        ai_desc = ""
        if ai:
            ai_desc = ai.get("scene", "") or ai.get("description", "")
            if ai_desc:
                ai_desc = self._strip_prefixes(ai_desc)
                # Clean out robotic language from alt text too
                ai_desc = _ROBOTIC_ADJ_RE.sub(" ", ai_desc)
                ai_desc = STRIP_WORDS.sub(" ", ai_desc)
                ai_desc = re.sub(r"\s+", " ", ai_desc).strip()

        if ai_desc:
            # Avoid repeating character name if AI description starts with it
            desc_lower = ai_desc.lower()
            if chars and chars[0].lower() in desc_lower[:30]:
                if media and media.lower() not in desc_lower:
                    parts = [f"{ai_desc} (from {media})"]
                else:
                    parts = [ai_desc]
            elif parts:
                parts.append(ai_desc[0].lower() + ai_desc[1:] if ai_desc else "")
            else:
                parts.append(ai_desc)

        # Add art style hint
        if style and style not in ("photograph",):
            style_label = style.replace("_", " ").title()
            if not any(style.lower() in p.lower() for p in parts):
                parts.append(f"({style_label.lower()})")

        text = ", ".join(p for p in parts if p)
        if not text:
            return ""

        # Capitalise and cap length
        text = text[0].upper() + text[1:]
        words = text.split()
        if len(words) > 30:
            text = " ".join(words[:30])
        return text.strip()

    def _build_tags(self, ai: Optional[dict], context: dict, image_path: Path) -> str:
        """Build comma-separated tags combining all sources.

        Character names and franchise are always included as tags, plus
        a combined multi-word tag (e.g. "gojo jujutsu kaisen") for better
        searchability.
        """
        tags: set = set()

        # --- Scraped metadata tags ---
        for t in context.get("clean_tags", []):
            clean = t.strip().lower()
            clean = STRIP_WORDS.sub("", clean).strip()
            if clean and len(clean) > 1:
                tags.add(clean)

        # --- Character and media tags (always present when detected) ---
        for char in context.get("characters", []):
            tags.add(char.lower())
            # Combined multi-word tag for searchability
            if context.get("media_name"):
                combo = f"{char.lower()} {context['media_name'].lower()}"
                tags.add(combo)
        if context.get("media_name"):
            tags.add(context["media_name"].lower())
        if context.get("media_type"):
            tags.add(context["media_type"])
        if context.get("art_style"):
            tags.add(context["art_style"].replace("_", " "))

        # --- Subject tags from metadata ---
        for subj in context.get("subjects", [])[:8]:
            s = subj.strip().lower()
            s = STRIP_WORDS.sub("", s).strip()
            if s and len(s) > 1:
                tags.add(s)

        # --- AI-generated tags ---
        if ai:
            for t in ai.get("tags", set()):
                if len(t) > 2 and t not in _STOP_WORDS:
                    tags.add(t)
            tags.update(ai.get("color_tags", set()))
            tags.update(ai.get("mood_tags", set()))
        else:
            # No AI — at least add colour/mood from the image
            try:
                img = Image.open(image_path).convert("RGB")
                color_tags = self._extract_color_tags(img)
                tags.update(color_tags)
                tags.update(self._mood_from_colors(color_tags))
            except Exception:
                pass

        # Remove junk tags that a human would never use
        tags.discard("mixed")
        tags -= _get_junk_tags()

        # Safety net: guarantee character + franchise are never displaced
        for char in context.get("characters", []):
            tags.add(char.lower())
        if context.get("media_name"):
            tags.add(context["media_name"].lower())

        # Sort: character/media names first, then alphabetical
        priority = set()
        for char in context.get("characters", []):
            priority.add(char.lower())
        if context.get("media_name"):
            priority.add(context["media_name"].lower())

        priority_tags = sorted(t for t in tags if t in priority)
        other_tags = sorted(t for t in tags if t not in priority)

        combined = priority_tags + other_tags
        return ", ".join(combined[:25])

    # ------------------------------------------------------------------
    # Text cleaning helpers
    # ------------------------------------------------------------------

    def _filter_cloud_result(
        self, title: str, alt: str, tags: str,
    ) -> Tuple[str, str, str]:
        """Apply word-filter settings to cloud AI output.

        The system prompt already instructs the model to avoid forbidden words,
        but models don't always obey — this enforces the same strip-words,
        robotic-adjective, junk-tag, and generic-prefix filters that the
        local BLIP pipeline uses.
        """
        # --- title ---
        title = self._strip_prefixes(title)
        title = _ROBOTIC_ADJ_RE.sub(" ", title)
        title = STRIP_WORDS.sub(" ", title)
        title = re.sub(r"\s+", " ", title).strip()
        # Restore Title Case after stripping
        if title:
            title = title[0].upper() + title[1:]

        # --- alt ---
        alt = self._strip_prefixes(alt)
        alt = _ROBOTIC_ADJ_RE.sub(" ", alt)
        alt = STRIP_WORDS.sub(" ", alt)
        alt = re.sub(r"\s+", " ", alt).strip()

        # --- tags ---
        junk = _get_junk_tags()
        tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
        cleaned_tags = []
        for t in tag_list:
            t = STRIP_WORDS.sub("", t).strip()
            if t and t not in junk and len(t) > 1:
                cleaned_tags.append(t)
        tags = ", ".join(cleaned_tags)

        return title, alt, tags

    def _clean_metadata_text(self, text: str) -> str:
        """Clean scraped title/alt/tag text — strip junk, site names, dimensions."""
        if not text:
            return ""
        if JUNK_PATTERN.search(text.strip()):
            return ""
        # Remove dimensions
        text = _DIMENSION_RE.sub("", text)
        # Remove site names / generic wallpaper words
        text = STRIP_WORDS.sub("", text)
        # Remove common title separators at the end: " | SiteName", " - SiteName.com"
        text = re.sub(r"\s*[|–—-]\s*\S+\.(com|net|org|io|cc)\b.*$", "", text, flags=re.I)
        text = re.sub(r"\s*[|–—-]\s*$", "", text)
        # Clean whitespace
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^[,.\-–—:;|]+\s*", "", text).strip()
        text = re.sub(r"[,.\-–—:;|]+\s*$", "", text).strip()
        if not text or len(text) < 2:
            return ""
        return text

    def _strip_prefixes(self, text: str) -> str:
        """Remove generic BLIP prefixes like 'a photo of'."""
        lower = text.lower()
        for prefix in _get_generic_prefixes():
            if lower.startswith(prefix):
                text = text[len(prefix):]
                break
        return text.strip()

    def _humanize_title(self, raw: str) -> str:
        """Distill a BLIP caption or scraped text into a human-like title.

        Strips robotic filler, then enriches flat descriptions with more
        evocative phrasing.  A human sharing a wallpaper writes creatively:
          "Crimson Horizon"  not  "Red sunset over ocean"
          "Gojo's Domain Expansion"  not  "Anime character with powers"

          BLIP: "a beautiful sunset over the ocean with mountains in the background"
          →     "Sunset over the Ocean"
        """
        text = self._strip_prefixes(raw)
        # Remove only robotic filler (not scene-descriptive phrases)
        text = _BLIP_FILLER_RE.sub(" ", text)
        text = _ING_FILLER_RE.sub(" ", text)
        text = _ROBOTIC_ADJ_RE.sub(" ", text)
        text = _TITLE_FILLER_RE.sub(" ", text)
        text = STRIP_WORDS.sub(" ", text)
        # Collapse whitespace and clean punctuation
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^[,.\-–—:;]+\s*", "", text).strip()
        text = re.sub(r"[,.\-–—:;]+\s*$", "", text).strip()
        # Drop redundant generic nouns that BLIP overuses
        text = re.sub(
            r"\ba\s+(man|woman|person|girl|boy|figure|individual)\s+",
            "", text, flags=re.I,
        )
        text = re.sub(
            r"\b(standing|sitting|looking)\s+(?=in|on|at|under|near|by)\b",
            "", text, flags=re.I,
        )
        text = re.sub(r"\s+", " ", text).strip()
        # Strip leading orphaned prepositions left after junk removal
        # e.g. "Free Stock Photo of Nature" → "of Nature" → "Nature"
        text = re.sub(r"^(of|for|from|with|by|about|to)\s+", "", text, flags=re.I)
        # Fix "an" before a consonant (left after adjective removal)
        # e.g. "an incredible sunset" → "an sunset" → "a sunset"
        text = re.sub(r"\ban\s+(?=[^aeiouAEIOU\s])", "a ", text)
        text = re.sub(r"\s+", " ", text).strip()
        # Strip trailing incomplete phrases left by BLIP token cutoff
        text = self._strip_trailing_danglers(text)
        if not text:
            return ""
        return self._cap(text, max_words=12)

    @staticmethod
    def _strip_trailing_danglers(text: str) -> str:
        """Remove trailing incomplete prepositional phrases.

        BLIP frequently ends output mid-phrase due to token limits:
          "standing in front of"  →  "standing"
          "flying over the"      →  "flying"
          "with a red and"       →  "with a red" → "with a red" (still ok)

        Also catches single trailing prepositions/articles.
        """
        # Multi-word danglers first (greedy — repeat until stable)
        prev = ""
        while text != prev:
            prev = text
            text = re.sub(
                r"\s+("
                r"in front of|on top of|next to|in the|on the|at the|"
                r"with the|with a|with an|from the|from a|"
                r"over the|under the|into the|"
                r"and the|and a|and an|or the|or a"
                r")\s*$",
                "", text, flags=re.I,
            )
        # Single trailing prepositions / articles / conjunctions
        text = re.sub(
            r"\s+(in|on|at|of|with|and|or|for|to|by|near|over|under|"
            r"the|a|an|into|from|through|across|behind|between)\s*$",
            "", text, flags=re.I,
        )
        return text.strip()

    @staticmethod
    def _cap(text: str, max_words: int = 12) -> str:
        """Title-case and truncate to max_words.

        Capitalises content words while keeping minor words (in, on, at …)
        lowercase — unless they're the first word.  Strips trailing minor
        words left over from truncation.
        """
        text = text.strip()
        if not text:
            return ""
        words = text.split()
        if len(words) > max_words:
            words = words[:max_words]
        # Strip trailing minor words left by truncation
        while len(words) > 1 and words[-1].lower() in _TITLE_MINOR_WORDS:
            words.pop()
        if not words:
            return text.split()[0].capitalize()
        # Title case: first word always capitalised, minor words stay lower
        titled = []
        for i, w in enumerate(words):
            if i == 0 or w.lower() not in _TITLE_MINOR_WORDS:
                titled.append(w[0].upper() + w[1:] if w else "")
            else:
                titled.append(w.lower())
        return " ".join(titled)

    # ------------------------------------------------------------------
    # Colour / mood analysis
    # ------------------------------------------------------------------

    def _extract_color_tags(self, img: Image.Image) -> set:
        """Extract dominant color names from image."""
        try:
            small = img.resize((50, 50), Image.LANCZOS)
            pixels = list(small.getdata())
            color_counts: dict = {}
            for r, g, b in pixels:
                name = self._classify_color(r, g, b)
                color_counts[name] = color_counts.get(name, 0) + 1

            total = len(pixels)
            tags: set = set()
            for color, count in sorted(color_counts.items(), key=lambda x: -x[1]):
                if count / total > 0.1:
                    tags.add(color)
                if len(tags) >= 3:
                    break
            return tags
        except Exception:
            return set()

    def _mood_from_colors(self, color_tags: set) -> set:
        """Generate aesthetic/mood tags from dominant colors."""
        mood: set = set()
        if "black" in color_tags or "gray" in color_tags:
            mood.add("moody")
        if "blue" in color_tags and "white" in color_tags:
            mood.add("serene")
        if "green" in color_tags:
            mood.add("nature")
        if "orange" in color_tags or "red" in color_tags:
            mood.add("warm")
        if "purple" in color_tags or "teal" in color_tags:
            mood.add("dreamy")
        if "white" in color_tags and len(color_tags) <= 2:
            mood.add("minimal")
        return mood

    def _classify_color(self, r: int, g: int, b: int) -> str:
        """Classify RGB into color name."""
        if r > 200 and g > 200 and b > 200:
            return "white"
        if r < 50 and g < 50 and b < 50:
            return "black"
        if r > 150 and g < 100 and b < 100:
            return "red"
        if r < 100 and g > 150 and b < 100:
            return "green"
        if r < 100 and g < 100 and b > 150:
            return "blue"
        if r > 200 and g > 200 and b < 100:
            return "yellow"
        if r > 200 and g > 100 and b < 50:
            return "orange"
        if r > 100 and g < 80 and b > 100:
            return "purple"
        if r > 150 and g > 150 and b > 150:
            return "gray"
        if r < 80 and g > 100 and b > 100:
            return "teal"
        return "mixed"

    def _fallback_caption(self, image_path: Path) -> Tuple[str, str, str]:
        """Generate basic fallback captions from image properties."""
        try:
            img = Image.open(image_path).convert("RGB")
            color_tags = self._extract_color_tags(img)
            mood_tags = self._mood_from_colors(color_tags)
            w, h = img.size

            all_tags = color_tags | mood_tags
            orientation = "landscape" if w > h else "portrait" if h > w else "square"

            if "nature" in mood_tags:
                title = random.choice(["Into the wild", "Nature's palette", "Green vibes"])
            elif "moody" in mood_tags:
                title = random.choice(["Dark aesthetics", "After dark", "Midnight tones"])
            elif "serene" in mood_tags:
                title = random.choice(["Clear skies", "Blue hour", "Calm waters"])
            elif "warm" in mood_tags:
                title = random.choice(["Golden hour", "Warm light", "Sunset vibes"])
            elif "dreamy" in mood_tags:
                title = random.choice(["Dreamscape", "Fading light", "Soft tones"])
            elif "minimal" in mood_tags:
                title = random.choice(["Less is more", "Clean lines", "Simplicity"])
            else:
                title = random.choice([
                    "Untitled",
                    f"{orientation.capitalize()} view",
                    "Found this gem",
                ])

            alt_text = f"{orientation.capitalize()} scene with {' and '.join(sorted(color_tags)[:2]) or 'mixed'} tones"

            return title, alt_text, ", ".join(sorted(all_tags))
        except Exception:
            return ("Untitled", "", "")

    @property
    def is_available(self) -> bool:
        return self._initialized
