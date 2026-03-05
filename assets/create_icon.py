"""
Generate a simple PNG icon for use as the application and tray icon.

Run this script once to create assets/icon.png.
"""

import os
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


def create_icon(size: int = 256) -> "Image.Image":
    """
    Draw a teal circle with a white 'R' letter at *size* × *size* pixels.

    Returns a PIL Image object.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle
    margin = size // 8
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(0, 150, 136),
    )

    # White 'R' text centred on the icon
    font_size = size // 2
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except (OSError, IOError):
        font = ImageFont.load_default()

    text = "R"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), text, fill="white", font=font)

    return img


def main() -> None:
    """Generate and save the icon to the assets directory."""
    assets_dir = Path(__file__).parent
    out_path = assets_dir / "icon.png"

    if not _PIL_AVAILABLE:
        print("Pillow is not installed – cannot generate icon.")
        return

    img = create_icon(256)
    img.save(out_path, format="PNG")
    print(f"Icon saved to {out_path}")


if __name__ == "__main__":
    main()
