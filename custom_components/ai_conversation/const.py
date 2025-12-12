"""Constants for the integration."""

import logging
import voluptuous as vol  # noqa
from typing import Any, Optional, Callable  # noqa
from homeassistant.core import HomeAssistant, callback  # noqa
from homeassistant.const import (  # noqa
    Platform,
    CONF_NAME, CONF_BASE, CONF_API_KEY, CONF_SERVICE,
    CONF_MODEL, CONF_LLM_HASS_API, MATCH_ALL,
    ATTR_ENTITY_ID,
)
from homeassistant.util import slugify  # noqa
from homeassistant.exceptions import HomeAssistantError  # noqa
from homeassistant.config_entries import ConfigEntry, ConfigSubentry  # noqa

DOMAIN = "ai_conversation"
LOGGER = logging.getLogger(__package__)

MAX_TOOL_ITERATIONS = 10
CONF_CUSTOM = "custom"
CONF_PROMPT = "prompt"

PLATFORMS = (
    Platform.CONVERSATION,
    Platform.TTS,
)
