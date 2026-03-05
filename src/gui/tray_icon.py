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
    Generate a simple colored circle image to use as the tray icon.

    Args:
        size: Edge length in pixels for the square image.

    Returns:
        A PIL Image object.
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Draw a blue circle as the icon
    draw.ellipse((4, 4, size - 4, size - 4), fill=(0, 120, 212, 255))
    # Draw a small white arrow inside the circle to suggest sync
    draw.polygon(
        [(size // 3, size // 4), (2 * size // 3, size // 2), (size // 3, 3 * size // 4)],
        fill=(255, 255, 255, 230),
    )
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
