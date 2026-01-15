import aiohttp
import json
from homeassistant.components.stt import (
    DOMAIN as ENTITY_DOMAIN,
    SpeechToTextEntity as BaseEntity,
    AudioCodecs,
    AudioFormats,
    AudioChannels,
    AudioBitRates,
    AudioSampleRates,
    SpeechMetadata,
    SpeechResult,
    SpeechResultState,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from collections.abc import AsyncIterable

from . import HassEntry, BasicEntity
from .const import *


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    for subentry_id, subentry in config_entry.subentries.items():
        if subentry.subentry_type != ENTITY_DOMAIN:
            continue
        entry = await HassEntry.async_init(hass, config_entry)
        async_add_entities(
            [SpeechToTextEntity(entry, subentry)],
            config_subentry_id=subentry_id,
        )

class SpeechToTextEntity(BasicEntity, BaseEntity):
    domain = ENTITY_DOMAIN
    _default_name = "ASR"

    def on_init(self):
        self._attr_supported_languages = [self.hass.config.language]
        self._attr_supported_codecs = [AudioCodecs.PCM, AudioCodecs.OPUS]
        self._attr_supported_formats = [AudioFormats.WAV, AudioFormats.OGG]
        self._attr_supported_channels = [x for x in AudioChannels]
        self._attr_supported_bit_rates = [x for x in AudioBitRates]
        self._attr_supported_sample_rates = [x for x in AudioSampleRates]
        self._attr_extra_state_attributes = {}
        self.session = async_get_clientsession(self.hass, verify_ssl=False)

    @property
    def supported_languages(self):
        return self._attr_supported_languages

    @property
    def supported_codecs(self):
        return self._attr_supported_codecs

    @property
    def supported_formats(self):
        return self._attr_supported_formats

    @property
    def supported_channels(self):
        return self._attr_supported_channels

    @property
    def supported_bit_rates(self):
        return self._attr_supported_bit_rates

    @property
    def supported_sample_rates(self):
        return self._attr_supported_sample_rates

    def get_extra(self, field=None):
        extra = self.subentry.data.get("extra_body") or {}
        if not isinstance(extra, dict):
            extra = {}
        if field:
            return extra.get(field)
        return extra

    async def async_process_audio_stream(
        self, metadata: SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> SpeechResult:
        audio_data = b"".join([chunk async for chunk in stream])
        LOGGER.info(
            "Processing audio stream: language=%s, format=%s, codec=%s, bit_rate=%s, sample_rate=%s, length=%s",
            metadata.language,
            metadata.format,
            metadata.codec,
            metadata.bit_rate,
            metadata.sample_rate,
            len(audio_data),
        )
        extra = self.get_extra()
        form = aiohttp.FormData({"model": self.model, **extra})
        form.add_field(
            "file", audio_data,
            content_type=f"audio/{metadata.format.value}",
            filename=f"audio.{metadata.format.value}",
        )
        resp = await self.entry.async_post("audio/transcriptions", data=form)
        text = await resp.text()
        if not text or resp.status != 200:
            LOGGER.warning("Failed to process audio stream: %s", [text, resp.status, resp.headers])
            return SpeechResult(text, SpeechResultState.ERROR)
        if str(text).startswith("{"):
            try:
                data = json.loads(text) or {}
                if txt := data.get("text"):
                    text = txt
                else:
                    LOGGER.warning("Failed to get text from json: %s", text)
                    return SpeechResult(text, SpeechResultState.ERROR)
            except Exception:
                LOGGER.warning("Failed to parse json: %s", text, exc_info=True)
        return SpeechResult(text, SpeechResultState.SUCCESS)
