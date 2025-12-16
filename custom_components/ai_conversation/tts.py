from aiohttp import web
from base64 import urlsafe_b64decode
from homeassistant.components.tts import (
    DOMAIN as ENTITY_DOMAIN,
    TextToSpeechEntity as BaseEntity,
    TtsAudioType,
    TTSAudioRequest,
    TTSAudioResponse,
    ATTR_VOICE,
    async_create_stream,
)
from homeassistant.const import ATTR_MODEL
from homeassistant.util import ulid
from collections.abc import AsyncGenerator
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.http import HomeAssistantView, KEY_HASS, KEY_AUTHENTICATED
from sentence_stream import async_stream_to_sentences

from . import HassEntry, BasicEntity
from .const import *

ATTR_GAIN = "gain"
ATTR_SPEED = "speed"
ATTR_FORMAT = "response_format"


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Set up conversation entities."""
    for subentry_id, subentry in config_entry.subentries.items():
        if subentry.subentry_type != ENTITY_DOMAIN:
            continue
        entry = await HassEntry.async_init(hass, config_entry)
        async_add_entities(
            [TextToSpeechEntity(entry, subentry)],
            config_subentry_id=subentry_id,
        )

    hass.http.register_view(AiTtsProxyView)
    hass.http.register_view(AiTtsProxyView(url=f"/api/tts_proxy/{DOMAIN}/{{filename:.*}}"))

class TextToSpeechEntity(BasicEntity, BaseEntity):
    domain = ENTITY_DOMAIN
    _default_name = "Speech"

    def on_init(self):
        self._attr_default_language = self.hass.config.language
        self._attr_supported_languages = [self.hass.config.language]
        self._attr_supported_options = [ATTR_VOICE, ATTR_MODEL, ATTR_SPEED, ATTR_GAIN, ATTR_FORMAT]
        self._attr_extra_state_attributes = {}
        self.session = async_get_clientsession(self.hass, verify_ssl=False)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        domain_data["tts_entity_id"] = self.entity_id
        access_tokens = domain_data.setdefault("access_tokens", {
            "temp": ulid.ulid_hex(),
            "long": self.hass.data["core.uuid"],
        })
        self._attr_extra_state_attributes["access_tokens"] = access_tokens.copy()

    def get_extra(self, field=None):
        extra = self.subentry.data.get("extra_body") or {}
        if not isinstance(extra, dict):
            extra = {}
        if field:
            return extra.get(field)
        return extra

    async def async_get_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> TtsAudioType:
        format = options.get(ATTR_FORMAT) or self.get_extra(ATTR_FORMAT) or "mp3"
        stream = await self._process_tts_audio(message, language, options)
        return (format, stream)

    async def _process_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ):
        stream = b""
        async for chunk in self._process_tts_audio_chunked(message, language, options):
            stream += chunk
        if not stream or stream[0:1] == b"{":
            LOGGER.warning("TTS error: %s", stream.decode())
            raise HomeAssistantError(f"TTS error: {stream.decode()}")
        return stream

    async def _process_tts_audio_chunked(
        self, message: str, language: str, options: dict[str, Any]
    ):
        extra = self.get_extra()
        res = await self.entry.async_post("audio/speech", {
            **extra,
            "input": message,
            "model": options.get(ATTR_MODEL) or extra.get(ATTR_MODEL) or self.model,
            "voice": options.get(ATTR_VOICE) or extra.get(ATTR_VOICE),
            "response_format": options.get(ATTR_FORMAT) or self.get_extra(ATTR_FORMAT) or "mp3",
        })
        res.raise_for_status()
        async for chunk in res.content.iter_chunked(1024 * 10):
            yield chunk

    async def async_stream_tts_audio(self, request: TTSAudioRequest) -> TTSAudioResponse:
        return TTSAudioResponse("mp3", self._process_tts_stream(request))

    async def _process_tts_stream(self, request: TTSAudioRequest) -> AsyncGenerator[bytes]:
        """Generate speech from an incoming message."""
        LOGGER.debug("Starting TTS Stream with options: %s", request.options)
        if self.subentry.data.get("full_input"):
            message = "".join([chunk async for chunk in request.message_gen])
            yield await self._process_tts_audio(message, request.language, request.options)
        else:
            separators = "\n。.，,；;！!？?、"
            buffer = ""
            count = 0
            async for message in request.message_gen:
                LOGGER.debug("Streaming tts sentence: %s", message)
                count += 1
                min_len = 2 ** count * 10
                for char in message:
                    buffer += char
                    msg = buffer.strip()
                    if len(msg) >= min_len and char in separators:
                        buffer = ""
                        async for chunk in self._process_tts_audio_chunked(msg, request.language, request.options):
                            yield chunk
            if msg := buffer.strip():
                yield await self._process_tts_audio(msg, request.language, request.options)


class AiTtsProxyView(HomeAssistantView):
    requires_auth = False
    cors_allowed = True
    url = f"/api/tts_proxy/{DOMAIN}"
    name = f"api:tts_proxy_{DOMAIN}"

    def __init__(self, url=None):
        if url:
            self.url = url

    async def get(self, request: web.Request, **kwargs) -> web.StreamResponse:
        hass = request.app[KEY_HASS]
        domain_data = hass.data.setdefault(DOMAIN, {})
        access_token = request.query.get("token")
        authenticated = request.get(KEY_AUTHENTICATED)
        if not authenticated and access_token:
            authenticated = access_token in domain_data.get("access_tokens", {}).values()
        if not authenticated:
            raise web.HTTPUnauthorized
        if not (message := request.query.get("message")):
            return self.json({"error": "message empty"}, 400)
        if message.startswith("base64:"):
            message = message[7:].replace(" ", "+")
            message = urlsafe_b64decode(message).decode()

        entity_id = request.query.get("entity_id") or domain_data.get("tts_entity_id")
        if not entity_id:
            return self.json({"error": "tts entity not found"}, 400)

        try:
            stream = async_create_stream(
                hass, entity_id,
                language=request.query.get("language"),
                options={
                    ATTR_VOICE: request.query.get(ATTR_VOICE),
                    ATTR_SPEED: request.query.get(ATTR_SPEED),
                },
            )
        except Exception as err:
            return self.json({"error": str(err)}, 400)

        stream.async_set_message(message)
        response: web.StreamResponse | None = None
        try:
            async for data in stream.async_stream_result():
                if response is None:
                    response = web.StreamResponse()
                    response.content_type = stream.content_type
                    await response.prepare(request)
                await response.write(data)
        except Exception as err:
            LOGGER.error("Error streaming tts", exc_info=True)
            return self.json({"error": str(err)}, 400)
        if response is None:
            return web.Response(status=500)
        await response.write_eof()
        return response
