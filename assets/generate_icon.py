"""
generate_icon.py
----------------
Generates the application icon (assets/icon.png and assets/icon.ico)
using Pillow.  Run this script once during the build process.
"""

import os
from pathlib import Path


def generate_icon():
    """Create a simple circular icon with the letter R for Rclone Manager."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Pillow not installed – skipping icon generation.")
        return

    # Output directory (same folder as this script)
    assets_dir = Path(__file__).parent
    assets_dir.mkdir(exist_ok=True)

    # Icon dimensions (multiple sizes baked into ICO)
    sizes = [16, 32, 48, 64, 128, 256]

    frames = []
    for size in sizes:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Background circle (blue)
        margin = int(size * 0.06)
        draw.ellipse(
            [margin, margin, size - margin, size - margin],
            fill="#2563EB",
        )

        # White "R" letter centred
        font_size = int(size * 0.5)
        try:
            # Try to load a bundled or system font
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

        # Calculate text position to centre it
        bbox = draw.textbbox((0, 0), "R", font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (size - tw) // 2
        y = (size - th) // 2
        draw.text((x, y), "R", fill="white", font=font)

        frames.append(img)

    # Save as PNG (largest size)
    png_path = assets_dir / "icon.png"
    frames[-1].save(str(png_path))
    print(f"Saved: {png_path}")

    # Save as ICO (Windows) with all sizes
    ico_path = assets_dir / "icon.ico"
    frames[0].save(
        str(ico_path),
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print(f"Saved: {ico_path}")

    # Save as ICNS placeholder (macOS) – actual ICNS needs external tool
    icns_png = assets_dir / "icon_mac.png"
    frames[-1].save(str(icns_png))
    print(f"Saved macOS placeholder: {icns_png}")


if __name__ == "__main__":
    generate_icon()
