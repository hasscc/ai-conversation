import logging
import re
import voluptuous as vol

from base64 import urlsafe_b64encode
from urllib.parse import urlencode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, config_validation as cv
from homeassistant.helpers.network import get_url
from homeassistant.components.media_player import browse_media, MediaPlayerEntityFeature

from .const import DOMAIN


_LOGGER = logging.getLogger(__name__)


async def async_setup_intents(hass: HomeAssistant):
    """Set up the intents."""
    intent.async_register(hass, AiConvertTextToSound())
    intent.async_register(hass, AiMediaPlayMediaUrl())
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


class AiMediaPlayMediaUrl(intent.IntentHandler):
    intent_type = "AiMediaPlayMediaUrl"
    description = "Play streaming media URL (such as: m3u8/mp4, etc.) on the media player entity"
    slot_schema = {
        vol.Required("play_url", description="Remote media URL, e.g., m3u8/mp4/mp3, etc."): cv.string,
        vol.Optional("media_type", description="Media type: `video`/`audio`/`music`, etc."): cv.string,
        vol.Optional("media_title"): cv.string,
        vol.Optional("media_thumb"): cv.string,
        vol.Optional("name", description="Media player entity name"): cv.string,
        vol.Optional("area"): cv.string,
        vol.Optional("floor"): cv.string,
        vol.Optional("preferred_area_id"): cv.string,
        vol.Optional("preferred_floor_id"): cv.string,
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)

        # Find matching entities
        match_constraints = intent.MatchTargetsConstraints(
            name=slots.get("name", {}).get("value"),
            area_name=slots.get("area", {}).get("value"),
            floor_name=slots.get("floor", {}).get("value"),
            domains={"media_player"},
            assistant=intent_obj.assistant,
            features=MediaPlayerEntityFeature.PLAY_MEDIA,
            single_target=True,
        )
        match_result = intent.async_match_targets(
            hass,
            match_constraints,
            intent.MatchTargetsPreferences(
                area_id=slots.get("preferred_area_id", {}).get("value"),
                floor_id=slots.get("preferred_floor_id", {}).get("value"),
            ),
        )
        if not match_result.is_match:
            raise intent.MatchFailedError(
                result=match_result, constraints=match_constraints
            )

        media_item = browse_media.BrowseMedia(
            media_class=browse_media.MediaClass.URL,
            media_content_id=slots["play_url"]["value"],
            media_content_type=slots.get("media_type", {}).get("value", ""),
            title=slots.get("media_title", {}).get("value", ""),
            thumbnail=slots.get("media_thumb", {}).get("value", ""),
            can_play=True,
            can_expand=False,
        )
        try:
            await hass.services.async_call(
                "media_player", "play_media",
                {
                    "entity_id": match_result.states[0].entity_id,
                    "media_content_id": media_item.media_content_id,
                    "media_content_type": media_item.media_content_type,
                },
                blocking=True,
                context=intent_obj.context,
            )
        except HomeAssistantError as err:
            _LOGGER.error("Error calling play_media: %s", err)
            raise intent.IntentHandleError(f"Error playing media: {err}") from err

        response = intent_obj.create_response()
        response.async_set_speech_slots({"media": media_item.as_dict()})
        return response
