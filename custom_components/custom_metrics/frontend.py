"""
Serve and auto-register the bundled custom Lovelace card.

Uses hass.http.async_register_static_paths to serve the built JS file and
homeassistant.components.frontend.add_extra_js_url to inject it globally, so
the card is available as `type: custom:custom-metrics-card` in any dashboard
without the user ever needing to add a Lovelace "Resource" manually.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CARD_URL_PATH = f"/{DOMAIN}/custom-metrics-card.js"
CARD_FILE_PATH = Path(__file__).parent / "www" / "custom-metrics-card.js"
_FRONTEND_REGISTERED_KEY = f"{DOMAIN}_frontend_registered"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Register the static path + extra JS module, once, hass-wide."""
    if hass.data.get(_FRONTEND_REGISTERED_KEY):
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_PATH, str(CARD_FILE_PATH), cache_headers=False)]
    )
    add_extra_js_url(hass, CARD_URL_PATH)
    hass.data[_FRONTEND_REGISTERED_KEY] = True
