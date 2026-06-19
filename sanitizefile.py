import re
import uuid
from pathlib import Path


def sanitize_filename(name: str) -> str:
    """
    Remove unsafe characters from filename.
    
    Keeps: letters, numbers, hyphens, underscores
    Replaces spaces with hyphens
    Removes everything else
    """
    # Replace spaces with hyphens
    name = name.replace(" ", "-")
    
    # Remove any character that isn't alphanumeric, hyphen, or underscore
    name = re.sub(r"[^a-zA-Z0-9\-_]", "", name)
    
    # Remove leading/trailing hyphens
    name = name.strip("-")
    
    # Lowercase
    name = name.lower()
    
    return name or "image"  # Fallback if everything was stripped


def generate_image_key(prefix: str, original_name: str | None) -> str:
    """
    Generate a unique, safe storage key for an image.
    
    Args:
        prefix: Folder prefix e.g "home/logo"
        original_name: Original filename from upload
        
    Returns:
        str: Safe unique key e.g "home/logo/company-logo-a1b2c3d4.png"
    
    Examples:
        generate_image_key("home/logo", "Company Logo!.png")
        → "home/logo/company-logo-a1b2c3d4.png"
        
        generate_image_key("home/logo", None)
        → "home/logo/unnamed-a1b2c3d4.jpeg"
        
        generate_image_key("home/logo", ".png")
        → "home/logo/image-a1b2c3d4.png"
        
        generate_image_key("home/logo", "my photo (1).jpg")
        → "home/logo/my-photo-1-a1b2c3d4.jpg"
    """
    if original_name is None:
        base_name = "unnamed"
        extension = ".jpeg"
    else:
        path = Path(original_name)
        raw_stem = path.stem or "image"
        base_name = sanitize_filename(raw_stem)  # ✅ Safe characters only
        extension = path.suffix.lower() or ".jpg"
    
    short_id = uuid.uuid4().hex[:8]
    unique_name = f"{base_name}-{short_id}{extension}"
    
    return f"{prefix}/{unique_name}"
