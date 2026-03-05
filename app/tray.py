"""
System tray icon manager.

Uses pystray to show a notification-area icon.  Clicking the icon
restores the main window; right-clicking shows a context menu.
"""

import threading
from typing import Callable, Optional

try:
    import pystray
    from pystray import MenuItem as TItem
    from PIL import Image, ImageDraw

    _TRAY_AVAILABLE = True
except Exception:
    # pystray may fail to load on headless systems or when the required
    # native toolkit (e.g. GTK on Linux) is not available at runtime.
    _TRAY_AVAILABLE = False


def _create_icon_image(size: int = 64) -> "Image.Image":
    """
    Generate a simple coloured circle as the tray icon.

    Returns a PIL Image object.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Draw a filled teal circle
    margin = size // 8
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(0, 150, 136),
    )
    # Draw a white 'R' in the centre
    draw.text((size // 3, size // 4), "R", fill="white")
    return img


class TrayIcon:
    """Wraps a pystray icon with show/hide callbacks for the main window."""

    def __init__(
        self,
        on_show: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        """
        Initialise the tray icon.

        Args:
            on_show: Called when the user clicks the tray icon or 'Mostrar'.
            on_quit: Called when the user selects 'Salir'.
        """
        self._on_show = on_show
        self._on_quit = on_quit
        self._icon: Optional["pystray.Icon"] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the tray icon in a background daemon thread."""
        if not _TRAY_AVAILABLE:
            return

        image = _create_icon_image()

        menu = pystray.Menu(
            TItem("Mostrar", self._handle_show, default=True),
            TItem("Salir", self._handle_quit),
        )

        self._icon = pystray.Icon(
            "RcloneManager",
            image,
            "Rclone Manager",
            menu,
        )

        self._thread = threading.Thread(
            target=self._icon.run,
            daemon=True,
            name="tray-thread",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop and remove the tray icon."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    def update_tooltip(self, text: str) -> None:
        """Update the tray icon tooltip text."""
        if self._icon is not None:
            self._icon.title = text

    # ------------------------------------------------------------------
    # Internal callbacks
    # ------------------------------------------------------------------

    def _handle_show(self, icon: "pystray.Icon", item: "TItem") -> None:
        """Handle tray 'Mostrar' click."""
        self._on_show()

    def _handle_quit(self, icon: "pystray.Icon", item: "TItem") -> None:
        """Handle tray 'Salir' click."""
        self.stop()
        self._on_quit()
