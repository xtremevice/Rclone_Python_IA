"""
System tray icon integration.

Provides a notification-area (system tray) icon that lets the user
restore the main window when it has been minimized.

Falls back gracefully if pystray or Pillow is not available.
"""

import threading
from typing import Callable, Optional

try:
    import pystray
    from pystray import MenuItem, Menu
    from PIL import Image, ImageDraw
    _TRAY_AVAILABLE = True
except (ImportError, ValueError, Exception):
    # Falls back gracefully when pystray or its system dependencies are missing
    _TRAY_AVAILABLE = False


def _create_icon_image(size: int = 64) -> "Image.Image":
    """
    Generate a Material Design-style sync icon for the system tray.

    The icon uses:
      - Material Blue 700 (#1976D2) circular background
      - Two white opposing arc arrows (Material Design 'sync' icon style)

    Args:
        size: Edge length in pixels for the square image.

    Returns:
        A PIL Image object.
    """
    import math

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # ── Background circle (Material Blue 700) ──────────────────────────
    pad = max(1, size // 16)
    draw.ellipse((pad, pad, size - pad, size - pad), fill=(25, 118, 210, 255))

    white = (255, 255, 255, 255)
    cx = size / 2
    cy = size / 2
    r = size * 0.30          # arc centre-line radius
    stroke = max(2, int(size * 0.09))
    bbox = [cx - r, cy - r, cx + r, cy + r]

    # Top arc (30° → 190°, clockwise ~160°)
    draw.arc(bbox, start=30, end=190, fill=white, width=stroke)
    # Bottom arc (210° → 10°, clockwise ~160°, wraps around)
    draw.arc(bbox, start=210, end=10, fill=white, width=stroke)

    # Arrowheads at the arc tips
    ah = int(stroke * 1.3)

    def _arrowhead(angle_deg: float, tangent_deg: float) -> None:
        a_rad = math.radians(angle_deg)
        tip_x = cx + r * math.cos(a_rad)
        tip_y = cy + r * math.sin(a_rad)
        t_rad = math.radians(tangent_deg)
        perp = math.radians(tangent_deg + 90)
        pts = [
            (tip_x + ah * math.cos(t_rad), tip_y + ah * math.sin(t_rad)),
            (tip_x - ah * 0.6 * math.cos(perp), tip_y - ah * 0.6 * math.sin(perp)),
            (tip_x + ah * 0.6 * math.cos(perp), tip_y + ah * 0.6 * math.sin(perp)),
        ]
        draw.polygon(pts, fill=white)

    _arrowhead(190, 280)   # end of top arc
    _arrowhead(10,  100)   # end of bottom arc

    return image


class TrayIcon:
    """
    Wraps pystray.Icon for system tray functionality.

    When the main window is minimized it is hidden and the tray icon
    becomes visible.  Clicking the tray icon or its 'Mostrar' menu entry
    calls the `on_show` callback so the main window can restore itself.
    """

    def __init__(
        self,
        on_show: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
    ) -> None:
        # Callbacks injected by the main window
        self._on_show = on_show
        self._on_quit = on_quit
        self._icon: Optional["pystray.Icon"] = None
        self._running = False

    def start(self) -> None:
        """Start the tray icon in a background daemon thread."""
        if not _TRAY_AVAILABLE:
            return
        if self._running:
            return

        image = _create_icon_image()

        # Build the context menu shown on right-click
        menu = Menu(
            MenuItem("Mostrar ventana", self._handle_show, default=True),
            Menu.SEPARATOR,
            MenuItem("Salir", self._handle_quit),
        )

        self._icon = pystray.Icon(
            name="RclonePythonIA",
            icon=image,
            title="Rclone Manager",
            menu=menu,
        )

        # Run the tray icon event loop in a background thread so it does
        # not block the Tkinter main loop
        thread = threading.Thread(target=self._icon.run, daemon=True)
        thread.start()
        self._running = True

    def stop(self) -> None:
        """Stop and remove the tray icon."""
        if self._icon and self._running:
            self._icon.stop()
            self._running = False

    def update_tooltip(self, text: str) -> None:
        """Update the tray icon tooltip text."""
        if self._icon:
            self._icon.title = text

    def is_available(self) -> bool:
        """Return True if pystray is installed and the tray icon can run."""
        return _TRAY_AVAILABLE

    # ------------------------------------------------------------------
    # Tray menu handlers
    # ------------------------------------------------------------------

    def _handle_show(self) -> None:
        """Called when the user clicks 'Mostrar ventana'."""
        if self._on_show:
            self._on_show()

    def _handle_quit(self) -> None:
        """Called when the user clicks 'Salir'."""
        self.stop()
        if self._on_quit:
            self._on_quit()
