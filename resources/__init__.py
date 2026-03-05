"""
Generador del ícono SVG de la aplicación para Rclone Python IA.
Proporciona el ícono en formato base64 para el sistema de bandeja.
"""

import base64


# Ícono SVG de la aplicación codificado en base64
# Representa dos flechas circulares (sincronización)
ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <circle cx="32" cy="32" r="30" fill="#2196F3" stroke="#1976D2" stroke-width="2"/>
  <path d="M32 14 A18 18 0 0 1 50 32" fill="none" stroke="white" stroke-width="4" stroke-linecap="round"/>
  <polygon points="50,24 54,36 42,34" fill="white"/>
  <path d="M32 50 A18 18 0 0 1 14 32" fill="none" stroke="white" stroke-width="4" stroke-linecap="round"/>
  <polygon points="14,40 10,28 22,30" fill="white"/>
</svg>"""

ICON_SVG_BYTES = ICON_SVG.encode("utf-8")
ICON_SVG_B64 = base64.b64encode(ICON_SVG_BYTES).decode("utf-8")


def get_icon_bytes() -> bytes:
    """Retorna los bytes del ícono SVG."""
    return ICON_SVG_BYTES


def get_icon_b64() -> str:
    """Retorna el ícono SVG en formato base64."""
    return ICON_SVG_B64
