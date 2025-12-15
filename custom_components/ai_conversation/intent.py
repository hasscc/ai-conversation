import logging
import re
import voluptuous as vol

from base64 import urlsafe_b64encode
from urllib.parse import urlencode
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.network import get_url

from .const import DOMAIN


_LOGGER = logging.getLogger(__name__)


async def async_setup_intents(hass: HomeAssistant):
    """Set up the intents."""
    intent.async_register(hass, AiConvertTextToSound())
    intents = hass.data.get("intent") or {}
    _LOGGER.info("Setup intents: %s", intents.keys())


class AiConvertTextToSound(intent.IntentHandler):
    intent_type = "AiConvertTextToSound"
    description = "Convert text to sound URL via Ai Agent"
    slot_schema = {
        vol.Required("message", description="The text to speak, don't include any line breaks, tabs, emoji."): intent.non_empty_string,
        vol.Optional("speed", description="Speech speed"): vol.All(vol.Coerce(float), vol.Range(0.25, 4.0)),
        vol.Optional("entity_id", description=f"TTS entity ID"): str,
        vol.Optional("filename", description="Audio file name, required"): str,
    }

    async def async_handle(self, intent_obj: intent.Intent):
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        message = slots.get("message", {}).get("value", "")
        message = str(message).replace("\n", " ").replace("\t", " ")

        pattern = r"[\r\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]"
        message = re.sub(pattern, "", message)

        filename = slots.get("filename", {}).get("value")

        domain_data = hass.data.setdefault(DOMAIN, {})
        token = domain_data.get("access_tokens", {}).get("temp", "")
        response = intent_obj.create_response()

        params = {
            "token": token,
            "message": "base64:" + urlsafe_b64encode(message.encode()).decode(),
            "speed": slots.get("speed", {}).get("value") or 1,
            "entity_id": slots.get("entity_id", {}).get("value") or "",
        }
        api = f"/api/tts_proxy/{DOMAIN}/{filename}?{urlencode(params)}"
        url = get_url(hass, prefer_external=True) + api

        response.response_type = intent.IntentResponseType.ACTION_DONE
        response.async_set_speech_slots({
            "tts_url": url,
            "notice": "This audio URL must remain intact, no parameters can be discarded; "
                      "the URL contains sensitive information and is not recommended to appear in any text content.",
        })
        return response
