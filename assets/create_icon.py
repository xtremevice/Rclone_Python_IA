"""
Generate a Material Design-style PNG icon for use as the application and tray icon.

Run this script once to create assets/icon.png.

Design follows Material Design guidelines:
  - Flat, geometric shapes on a solid background circle
  - Material Blue 700 (#1976D2) background
  - White foreground graphics (cloud + sync arrows)
  - Clear padding (keyline grid)
"""

import math
from pathlib import Path

try:
    from PIL import Image, ImageDraw
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


def _draw_sync_arrows(draw: "ImageDraw.ImageDraw", cx: float, cy: float,
                      radius: float, stroke: int, color: tuple) -> None:
    """
    Draw a Material Design-style circular sync symbol.

    Two opposing arcs, each spanning ~160°, with arrowheads at their tips,
    matching the appearance of the Google Material Icons 'sync' icon.

    Args:
        draw: An ImageDraw instance to draw on.
        cx, cy: Centre of the sync symbol.
        radius: Outer radius of the arcs (to the arc centre-line).
        stroke: Line width in pixels.
        color: Fill colour as an RGBA tuple.
    """
    r = radius  # arc centre-line radius
    bbox = [cx - r, cy - r, cx + r, cy + r]

    # Top arc: 30° → 190° (clockwise, ~160°)
    draw.arc(bbox, start=30, end=190, fill=color, width=stroke)
    # Bottom arc: 210° → 10° (clockwise, ~160°, wraps around)
    draw.arc(bbox, start=210, end=10, fill=color, width=stroke)

    # Arrowhead size relative to stroke
    ah = int(stroke * 1.4)

    def _arrowhead(angle_deg: float, tangent_deg: float) -> None:
        """Place a filled-triangle arrowhead on the arc."""
        a_rad = math.radians(angle_deg)
        tip_x = cx + r * math.cos(a_rad)
        tip_y = cy + r * math.sin(a_rad)

        # The triangle points along the tangent direction
        t_rad = math.radians(tangent_deg)
        # Three vertices: tip, and two base corners perpendicular to tangent
        perp = math.radians(tangent_deg + 90)
        pts = [
            (tip_x + ah * math.cos(t_rad),
             tip_y + ah * math.sin(t_rad)),
            (tip_x - ah * 0.6 * math.cos(perp),
             tip_y - ah * 0.6 * math.sin(perp)),
            (tip_x + ah * 0.6 * math.cos(perp),
             tip_y + ah * 0.6 * math.sin(perp)),
        ]
        draw.polygon(pts, fill=color)

    # Arrowhead at end of top arc (190°, CW tangent = 190° + 90° = 280°)
    _arrowhead(190, 280)
    # Arrowhead at end of bottom arc (10°, CW tangent = 10° + 90° = 100°)
    _arrowhead(10, 100)


def create_icon(size: int = 256) -> "Image.Image":
    """
    Draw a Material Design-style cloud-sync icon at *size* × *size* pixels.

    The icon consists of:
      - A round Material Blue 700 background circle
      - A white cloud shape in the upper-centre
      - A white circular sync arrow pair in the lower-centre

    Returns a PIL Image object.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Background circle (Material Blue 700) ──────────────────────────
    margin = size // 8
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(25, 118, 210, 255),  # #1976D2
    )

    white = (255, 255, 255, 255)

    # ── White cloud shape (upper half) ────────────────────────────────
    # Main dome + two side bumps + rectangular base
    cloud_cx = size * 0.50
    cloud_cy = size * 0.355
    dome_r = size * 0.12      # main dome radius
    left_r = dome_r * 0.72
    right_r = dome_r * 0.68

    # Positions of the three bumps
    left_cx  = cloud_cx - dome_r * 0.72
    left_cy  = cloud_cy + dome_r * 0.22
    right_cx = cloud_cx + dome_r * 0.82
    right_cy = cloud_cy + dome_r * 0.26

    def _circle(cx, cy, r):
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=white)

    # Cloud base rectangle (fills the gap between the bumps at the bottom)
    base_x0 = left_cx - left_r
    base_x1 = right_cx + right_r
    base_y0 = max(left_cy, cloud_cy, right_cy) - dome_r * 0.05
    base_y1 = base_y0 + dome_r * 0.80
    draw.rectangle([base_x0, base_y0, base_x1, base_y1], fill=white)

    # Three bumps on top of the base
    _circle(left_cx, left_cy, left_r)
    _circle(cloud_cx, cloud_cy, dome_r)
    _circle(right_cx, right_cy, right_r)

    # ── White sync arrows (lower half) ────────────────────────────────
    sync_cx = size * 0.50
    sync_cy = size * 0.685
    sync_r  = size * 0.135
    stroke  = max(2, int(size * 0.055))

    _draw_sync_arrows(draw, sync_cx, sync_cy, sync_r, stroke, white)

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
    print(f"Material Design icon saved to {out_path}")


if __name__ == "__main__":
    main()
