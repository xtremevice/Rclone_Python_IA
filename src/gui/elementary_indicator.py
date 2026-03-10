"""
Elementary OS Wingpanel indicator integration.

On Elementary OS this module creates an AppIndicator3-based indicator
that is permanently visible in Wingpanel while the application is
running (not only when the main window is minimised).

The context menu offers two actions:
  - "Mostrar ventana" → restores / raises the main window.
  - "Cerrar"          → quits the application.

Gracefully returns *unavailable* when:
  - The current OS is not Elementary OS, **or**
  - Neither ``AyatanaAppIndicator3`` nor ``AppIndicator3`` is installed.
"""

import os
import tempfile
import threading
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------

def is_elementary_os() -> bool:
    """Return *True* when the running OS is Elementary OS.

    Elementary OS sets ``ID=elementary`` in ``/etc/os-release``.
    """
    try:
        with open("/etc/os-release") as fh:
            content = fh.read().lower()
        # Elementary OS uses ID=elementary; could also appear in ID_LIKE
        return "id=elementary" in content or "elementary os" in content
    except OSError:
        return False


# ---------------------------------------------------------------------------
# AppIndicator3 import helper
# ---------------------------------------------------------------------------

def _import_app_indicator():
    """Try to import *AyatanaAppIndicator3* or *AppIndicator3*.

    Returns the imported module or *None* when neither is available.
    Prefers AyatanaAppIndicator3 (the modern ayatana fork used by
    modern Ubuntu/Elementary installations).
    """
    for ns in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            import gi  # noqa: PLC0415
            gi.require_version(ns, "0.1")
            mod = getattr(__import__("gi.repository", fromlist=[ns]), ns)
            return mod
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Icon helper
# ---------------------------------------------------------------------------

def _save_indicator_icon() -> Optional[str]:
    """Save the application icon to a temporary PNG file.

    AppIndicator3's ``set_icon_full`` accepts an absolute file path, so we
    write the PIL image used by TrayIcon to ``/tmp`` once per process.

    Returns the absolute path to the PNG, or *None* on failure.
    """
    try:
        from PIL import Image, ImageDraw  # noqa: PLC0415
        import math  # noqa: PLC0415

        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        pad = max(1, size // 16)
        draw.ellipse((pad, pad, size - pad, size - pad), fill=(25, 118, 210, 255))

        white = (255, 255, 255, 255)
        cx = size / 2
        cy = size / 2
        r = size * 0.30
        stroke = max(2, int(size * 0.09))
        bbox = [cx - r, cy - r, cx + r, cy + r]
        draw.arc(bbox, start=30, end=190, fill=white, width=stroke)
        draw.arc(bbox, start=210, end=10, fill=white, width=stroke)
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

        _arrowhead(190, 280)
        _arrowhead(10, 100)

        # Save to a stable tmp path so we don't create a new file on each run
        path = os.path.join(tempfile.gettempdir(), "rclone_python_ia_tray.png")
        # PNG supports RGBA directly
        image.save(path, "PNG")
        return path
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ElementaryIndicator
# ---------------------------------------------------------------------------

class ElementaryIndicator:
    """AppIndicator3-based Wingpanel indicator for Elementary OS.

    The indicator is started as soon as the application launches so that
    the icon is always visible in Wingpanel while Rclone Manager is
    running — not only when the main window is minimised.

    GTK's own main loop (``Gtk.main()``) runs in a dedicated daemon
    thread alongside Tkinter's event loop.  Cross-thread communication
    back to Tkinter is done via ``Tk.after(0, callback)`` to ensure all
    Tkinter operations execute on the main thread.
    """

    def __init__(
        self,
        on_show: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_show = on_show
        self._on_quit = on_quit
        self._indicator = None
        self._running = False
        self._gtk_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public API (mirrors TrayIcon so MainWindow can use either)
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return *True* if the indicator can run on the current system."""
        if not is_elementary_os():
            return False
        return _import_app_indicator() is not None

    def is_running(self) -> bool:
        """Return *True* if the indicator GTK loop is currently active."""
        return self._running

    def start(self) -> None:
        """Start the AppIndicator3 and its GTK event loop in a daemon thread."""
        if self._running:
            return

        ai_mod = _import_app_indicator()
        if ai_mod is None:
            return

        try:
            import gi  # noqa: PLC0415
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk  # type: ignore  # noqa: PLC0415

            # ── Context menu ──────────────────────────────────────────
            menu = Gtk.Menu()

            item_show = Gtk.MenuItem(label="Mostrar ventana")
            item_show.connect("activate", self._on_show_clicked)
            menu.append(item_show)

            menu.append(Gtk.SeparatorMenuItem())

            item_quit = Gtk.MenuItem(label="Cerrar")
            item_quit.connect("activate", self._on_quit_clicked)
            menu.append(item_quit)

            menu.show_all()

            # ── Indicator ─────────────────────────────────────────────
            # Try to use the saved PNG icon; fall back to a generic named icon.
            icon_path = _save_indicator_icon()
            icon_name = icon_path if (icon_path and os.path.isfile(icon_path)) else "indicator-messages"

            self._indicator = ai_mod.Indicator.new(
                "rclone-python-ia",
                icon_name,
                ai_mod.IndicatorCategory.APPLICATION_STATUS,
            )
            self._indicator.set_status(ai_mod.IndicatorStatus.ACTIVE)
            self._indicator.set_menu(menu)
            # Title shown in some desktop environments as a tooltip
            if hasattr(self._indicator, "set_title"):
                self._indicator.set_title("Rclone Manager")

            # ── GTK main loop in a daemon thread ──────────────────────
            def _gtk_main() -> None:
                Gtk.main()

            self._gtk_thread = threading.Thread(
                target=_gtk_main,
                daemon=True,
                name="gtk-wingpanel-indicator",
            )
            self._gtk_thread.start()
            self._running = True

        except Exception:
            # Any error (missing library, display issue, etc.) — skip silently.
            self._running = False
            self._indicator = None

    def stop(self) -> None:
        """Quit the GTK main loop, removing the indicator from Wingpanel."""
        if not self._running:
            return
        self._running = False
        try:
            import gi  # noqa: PLC0415
            gi.require_version("Gtk", "3.0")
            from gi.repository import GLib  # type: ignore  # noqa: PLC0415
            GLib.idle_add(self._gtk_quit)
        except Exception:
            pass

    def update_tooltip(self, text: str) -> None:
        """Update the indicator title (visible in some environments)."""
        if self._indicator and hasattr(self._indicator, "set_title"):
            try:
                self._indicator.set_title(text)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # GTK signal handlers (run on the GTK thread)
    # ------------------------------------------------------------------

    def _on_show_clicked(self, _widget) -> None:
        """Called when the user chooses "Mostrar ventana"."""
        if self._on_show:
            self._on_show()

    def _on_quit_clicked(self, _widget) -> None:
        """Called when the user chooses "Cerrar"."""
        self.stop()
        if self._on_quit:
            self._on_quit()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _gtk_quit() -> bool:
        """Quit the GTK main loop (called via GLib.idle_add on the GTK thread)."""
        try:
            from gi.repository import Gtk  # type: ignore  # noqa: PLC0415
            Gtk.main_quit()
        except Exception:
            pass
        return False  # Remove the idle handler
