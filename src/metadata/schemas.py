"""Metadata schemas for wallpaper data."""
from typing import Optional
from pydantic import BaseModel


DEFAULT_FIELD_MAPPING = {
    "wallpaperTitle": "wallpaperTitle",
    "Width": "Width",
    "Height": "Height",
    "imgUrl": "imgUrl",
    "altText": "Alt Text",
    "artistText": "Artist text",
    "artistLink": "Artist link",
    "isMobile": "IsMobile",
    "isReported": "IsReported",
    "isVip": "IsVip",
    "orgUploadDate": "OrgUploadDate",
    "categoryTags": "CategoryTags",
    "imageFile": "imageFile",
    "imgHash": "imgHash",
}


class WallpaperMetadata(BaseModel):
    """Metadata for a scraped wallpaper."""
    wallpaperTitle: str = ""
    Width: int = 0
    Height: int = 0
    imgUrl: str = ""
    altText: str = ""
    artistText: str = ""
    artistLink: str = ""
    isMobile: bool = False
    isReported: bool = False
    isVip: bool = False
    orgUploadDate: Optional[str] = None
    categoryTags: str = ""
    imageFile: Optional[list] = None
    imgHash: str = ""
    aspect_ratio: str = ""

    def to_baserow_dict(self, field_mapping: Optional[dict] = None) -> dict:
        """Convert to Baserow row dict using user's custom field mapping."""
        mapping = field_mapping or DEFAULT_FIELD_MAPPING
        result = {}
        result[mapping.get("wallpaperTitle", "wallpaperTitle")] = self.wallpaperTitle
        result[mapping.get("Width", "Width")] = self.Width
        result[mapping.get("Height", "Height")] = self.Height
        result[mapping.get("imgUrl", "imgUrl")] = self.imgUrl
        result[mapping.get("altText", "Alt Text")] = self.altText
        result[mapping.get("artistText", "Artist text")] = self.artistText
        result[mapping.get("artistLink", "Artist link")] = self.artistLink
        result[mapping.get("isMobile", "IsMobile")] = self.isMobile
        result[mapping.get("isReported", "IsReported")] = self.isReported
        result[mapping.get("isVip", "IsVip")] = self.isVip
        result[mapping.get("orgUploadDate", "OrgUploadDate")] = self.orgUploadDate
        result[mapping.get("categoryTags", "CategoryTags")] = self.categoryTags
        if self.imageFile is not None:
            result[mapping.get("imageFile", "imageFile")] = self.imageFile
        result[mapping.get("imgHash", "imgHash")] = self.imgHash
        return result
