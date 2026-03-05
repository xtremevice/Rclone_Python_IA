"""
RclonePyIA - Multiplatform Rclone Manager
Entry point for the application.
"""
import sys
from PyQt5.QtWidgets import QApplication
from src.app import RcloneApp


if __name__ == "__main__":
    # Create the Qt application
    app = QApplication(sys.argv)
    app.setApplicationName("RclonePyIA")
    app.setApplicationVersion("1.0.0")
    # Do not close app when last window is closed (lives in tray)
    app.setQuitOnLastWindowClosed(False)

    # Start the RcloneApp controller
    rclone_app = RcloneApp(app)
    rclone_app.start()

    sys.exit(app.exec_())
