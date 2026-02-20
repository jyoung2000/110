"""Download BLIP model at build time so it's cached in the Docker image."""
import sys

try:
    from transformers import BlipProcessor, BlipForConditionalGeneration
    print("Downloading BLIP-base model...")
    processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
    model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")
    print("BLIP-base model downloaded successfully")
except Exception as e:
    print(f"Warning: Could not download model: {e}", file=sys.stderr)
    print("Model will be downloaded on first use", file=sys.stderr)
