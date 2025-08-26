"""The Conversation integration."""
from __future__ import annotations

from aiohttp import hdrs
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from . import http
from .const import *
from .schemas import *
from .services import ServiceManager


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration."""
    hass.data.setdefault(DOMAIN, {})
    http.async_register(hass)
    ServiceManager(hass).setup_explain_media()
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    await HassEntry.async_init(hass, config_entry)
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    config_entry.async_on_unload(config_entry.add_update_listener(async_reload_entry))
    return True

async def async_reload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Handle update."""
    await hass.config_entries.async_reload(config_entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload Config Entry."""
    entry = await HassEntry.async_init(hass, config_entry)
    return await entry.async_unload()


class HassEntry:
    ALL: dict[str, "HassEntry"] = {}
    client = None
    session = None

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.id = entry.entry_id
        self.hass = hass
        self.entry = entry
        self.entities = {}

    @staticmethod
    async def async_init(hass: HomeAssistant, entry: ConfigEntry):
        this = HassEntry.ALL.get(entry.entry_id)
        if not this:
            this = HassEntry(hass, entry)
            HassEntry.ALL[this.id] = this
        return this

    async def async_unload(self):
        ret = await self.hass.config_entries.async_unload_platforms(self.entry, PLATFORMS)
        if ret:
            HassEntry.ALL.pop(self.id, None)
        return ret

    def __getattr__(self, item):
        return getattr(self.entry, item, None)

    def get_config(self, key=None, default=None):
        dat = {
            **self.entry.data,
            **self.entry.options,
        }
        if key:
            return dat.get(key, default)
        return dat

    def get_http_session(self):
        if self.session:
            return self.session
        base_url = self.get_config(CONF_BASE).rstrip('/')
        self.session = async_create_clientsession(self.hass, base_url=f"{base_url}/")
        return self.session

    async def async_chat_completions(self, data: ChatCompletions):
        http = self.get_http_session()
        headers = {
            hdrs.AUTHORIZATION: f"Bearer {self.get_config(CONF_API_KEY)}",
        }
        res = await http.post('chat/completions', json=data, headers=headers)
        result = ChatCompletionsResult(await res.json())
        result.response = res
        return result


class BasicEntity(Entity):
    domain = DOMAIN

    def __init__(self, entry: HassEntry, subentry: Optional[ConfigSubentry] = None):
        self.hass = entry.hass
        self.entry = entry
        self.subentry = subentry
        self.on_init()

    def on_init(self):
        pass

    async def async_added_to_hass(self):
        self.entry.entities[self.entity_id] = self