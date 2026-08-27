"""Tests for custom_metrics.frontend (card auto-registration)."""

from __future__ import annotations

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.custom_metrics.const import DOMAIN
from custom_components.custom_metrics.frontend import (
    CARD_URL_PATH,
    async_register_frontend,
)

from .conftest import async_setup_entry_with_types


async def test_card_url_registered(hass: HomeAssistant) -> None:
    """The card's module URL is registered for automatic loading."""
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "frontend", {})

    await async_register_frontend(hass)

    assert CARD_URL_PATH in hass.data[DATA_EXTRA_MODULE_URL].urls


async def test_register_frontend_is_idempotent(hass: HomeAssistant) -> None:
    """Calling async_register_frontend twice does not raise or duplicate."""
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "frontend", {})

    await async_register_frontend(hass)
    await async_register_frontend(hass)

    urls = [
        url for url in hass.data[DATA_EXTRA_MODULE_URL].urls if url == CARD_URL_PATH
    ]
    assert len(urls) == 1


async def test_entry_setup_registers_card(hass: HomeAssistant) -> None:
    """Setting up the config entry registers the card without manual steps."""
    await async_setup_entry_with_types(hass)

    assert hass.data.get(f"{DOMAIN}_frontend_registered") is True
    assert CARD_URL_PATH in hass.data[DATA_EXTRA_MODULE_URL].urls
