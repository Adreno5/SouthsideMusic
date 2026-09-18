from __future__ import annotations

from . import eapi


def getPersonalFM() -> dict:
    """Get private roaming / personal FM songs."""
    return eapi('/api/v1/radio/get', {'imageFm': '1'})
