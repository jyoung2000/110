"""Deduplication via Baserow hash lookup."""
from typing import Optional
import httpx
from src.utils.logging import setup_logging

logger = setup_logging("dedup")


class DedupChecker:
    """Check if a wallpaper already exists in Baserow by image hash."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=True,
            )
        return self._client

    async def is_duplicate(
        self,
        img_hash: str,
        api_url: str,
        table_id: int,
        api_token: str,
        field_mapping: Optional[dict] = None,
    ) -> bool:
        """Check if image hash already exists in Baserow table."""
        try:
            hash_field = "imgHash"
            if field_mapping:
                hash_field = field_mapping.get("imgHash", "imgHash")

            client = await self._get_client()
            url = f"{api_url}/api/database/rows/table/{table_id}/"
            params = {
                "user_field_names": "true",
                f"filter__{hash_field}__equal": img_hash,
                "size": 1,
            }
            headers = {"Authorization": f"Token {api_token}"}

            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            is_dupe = data.get("count", 0) > 0

            if is_dupe:
                logger.debug(f"Duplicate found: {img_hash}")
            return is_dupe

        except Exception as e:
            logger.warning(f"Dedup check failed for {img_hash}: {e}")
            return False

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
