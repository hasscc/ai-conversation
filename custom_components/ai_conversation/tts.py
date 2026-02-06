import io
import wave
from aiohttp import web
from base64 import urlsafe_b64decode
from homeassistant.components.tts import (
    DOMAIN as ENTITY_DOMAIN,
    TextToSpeechEntity as BaseEntity,
    TtsAudioType,
    TTSAudioRequest,
    TTSAudioResponse,
    DATA_TTS_MANAGER,
    ATTR_VOICE,
)
from homeassistant.const import ATTR_MODEL
from homeassistant.util import ulid
from homeassistant.components.http import HomeAssistantView, KEY_HASS, KEY_AUTHENTICATED
from collections.abc import AsyncGenerator

from . import HassEntry, BasicEntity
from .const import *

ATTR_GAIN = "gain"
ATTR_SPEED = "speed"
ATTR_FORMAT = "response_format"
SUPPORTED_OPTIONS = [ATTR_VOICE, ATTR_MODEL, ATTR_SPEED, ATTR_GAIN, ATTR_FORMAT]


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
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
        self._attr_supported_options = [*SUPPORTED_OPTIONS]
        self._attr_extra_state_attributes = {}

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

    def get_response_format(self, options: dict):
        return (
            options.get(ATTR_FORMAT) or
            self.get_extra(ATTR_FORMAT) or
            self.get_extra("response_format") or
            ""
        )

    async def async_get_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> TtsAudioType:
        stream = await self._process_tts_audio(message, language, options)
        format = self.get_response_format(options) or "wav"
        return (format, stream)

    async def async_stream_tts_audio(self, request: TTSAudioRequest) -> TTSAudioResponse:
        format = self.get_response_format(request.options) or "wav"
        return TTSAudioResponse(format, self._process_tts_stream(request))

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
        params = {
            **self.get_extra(),
            "input": message,
        }
        if model := options.get(ATTR_MODEL) or params.get(ATTR_MODEL) or self.model:
            params[ATTR_MODEL] = model
        if voice := options.get(ATTR_VOICE) or params.get(ATTR_VOICE, ""):
            params[ATTR_VOICE] = voice
        if speed := options.get(ATTR_SPEED) or params.get(ATTR_SPEED, ""):
            params[ATTR_SPEED] = speed
        if val := options.get(ATTR_FORMAT) or params.get(ATTR_FORMAT, ""):
            params["response_format"] = val
        res = await self.entry.async_post("audio/speech", params)
        LOGGER.debug("TTS request: %s", [params, str(res.request_info)])
        res.raise_for_status()
        if res.content_type in ["audio/mp3", "audio/mpeg"]:
            options[ATTR_FORMAT] = "mp3"
        if res.content_type == "audio/wav":
            options[ATTR_FORMAT] = "wav"
        if not res.content_type.startswith("audio/"):
            LOGGER.warning("Unexpected content type: %s, %s", res.content_type, await res.text())
            yield b""
        else:
            LOGGER.debug("TTS response format: %s", res.content_type)
            async for chunk in res.content.iter_any():
                yield chunk

    async def _process_tts_stream(self, request: TTSAudioRequest) -> AsyncGenerator[bytes]:
        """Generate speech from an incoming message."""
        LOGGER.debug("Starting TTS Stream with options: %s", request.options)
        if self.subentry.data.get("full_input"):
            message = "".join([chunk async for chunk in request.message_gen])
            yield await self._process_tts_audio(message, request.language, request.options)
        else:
            header_sent = False
            async for sentence in self.spilt_sentences(request.message_gen):
                LOGGER.debug("Streaming tts sentence: %s", sentence)
                audio_gen = self._process_tts_audio_chunked(sentence, request.language, request.options)
                async for chunk in self.fix_wav_header(audio_gen, header_sent):
                    header_sent = True
                    yield chunk

    async def fix_wav_header(self, stream, header_sent=None):
        async for chunk in stream:
            if chunk.startswith(b"RIFF") and b"WAVE" in chunk:
                with io.BytesIO(chunk) as f, wave.open(f, 'rb') as w:
                    chunk = w.readframes(w.getnframes())
                    if not header_sent:
                        header_buf = io.BytesIO()
                        with wave.open(header_buf, 'wb') as out_w:
                            out_w.setparams(w.getparams())
                            out_w.setnframes(0)
                        header = bytearray(header_buf.getvalue())
                        header[ 4: 8] = b'\xff\xff\xff\xff'
                        header[40:44] = b'\xff\xff\xff\xff'
                        chunk = header + chunk
            yield chunk

    async def spilt_sentences(self, message_gen):
        separators = ["\n", "。", ". ", "，", ", ", "；", "; ", "！", "! ", "？", "? ", "、"]
        buffer = ""
        count = 0
        async for message in message_gen:
            LOGGER.debug("Streaming tts message: %s", message)
            count += 1
            min_len = 2 ** count * 10
            for char in message:
                buffer += char
                msg = buffer.strip()
                if len(msg) < min_len:
                    continue
                if char in separators or buffer[-2:] in separators:
                    buffer = ""
                    yield msg
        if msg := buffer.strip():
            yield msg


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

        options = {}
        for attr in SUPPORTED_OPTIONS:
            if (val := request.query.get(attr)) is not None:
                options[attr] = val
        nocache = request.query.get("nocache")
        use_cache = None if nocache is None else (not nocache)
        LOGGER.debug("TTS api options: %s, use_cache: %s", options, use_cache)

        try:
            stream = hass.data[DATA_TTS_MANAGER].async_create_result_stream(
                engine=entity_id,
                use_file_cache=use_cache,
                language=request.query.get("language"),
                options=options,
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
