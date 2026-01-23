"""The Conversation integration."""
from __future__ import annotations

from aiohttp import hdrs
from homeassistant.helpers.entity import Entity, async_generate_entity_id
from homeassistant.helpers import device_registry as dr
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

    def get_http_headers(self, headers=None):
        extra = {}
        if apikey := self.get_config(CONF_API_KEY):
            extra[hdrs.AUTHORIZATION] = f"Bearer {apikey}"
        return {
            **extra,
            **(headers or {}),
        }

    async def async_post(self, api, json_data=None, **kwargs):
        http = self.get_http_session()
        headers = self.get_http_headers()
        LOGGER.debug("POST to %s: %s", api, json_data)
        return await http.post(api, json=json_data, headers=headers, **kwargs)

    async def async_chat_completions(self, data: ChatCompletions):
        res = await self.async_post("chat/completions", data)
        result = ChatCompletionsResult(await res.json())
        result.response = res
        return result


class BasicEntity(Entity):
    domain = DOMAIN
    _object_id = None
    _default_name = "Agent"

    def __init__(self, entry: HassEntry, subentry: Optional[ConfigSubentry] = None):
        self.hass = entry.hass
        self.entry = entry
        self.subentry = subentry
        self.model = self.subentry.data.get(CONF_MODEL, "")
        name = self.subentry.data.get(CONF_NAME) or self._default_name
        self._attr_name = f"{name} ({self.model})"
        self._attr_unique_id = f'{self.domain}.{self.subentry.subentry_id}'
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, self.subentry.subentry_id)},
            name=self.subentry.title,
            model=self.model,
            manufacturer=entry.title,
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        self.on_init()
        if self._object_id is None:
            self._object_id = slugify(self._default_name) + "_{}"
        self.entity_id = async_generate_entity_id(
            f'{self.domain}.{self._object_id}',
            name=self.model,
            hass=self.hass,
        )

    def on_init(self):
        pass

    async def async_added_to_hass(self):
        self.entry.entities[self.entity_id] = self

    async def _async_handle_chat_log(
        self,
        chat_log: conversation.ChatLog,
        structure_name: str | None = None,
        structure: vol.Schema | None = None,
    ) -> None:
        data = ChatCompletions(
            model=self.model,
            user=chat_log.conversation_id,
        )

        for content in chat_log.content:
            if msg := ChatMessage.from_conversation_content(content):
                data.messages.append(msg)

        if chat_log.llm_api:
            for tool in chat_log.llm_api.tools:
                func = ChatTool.from_hass_llm_tool(tool, chat_log.llm_api.custom_serializer)
                data.tools.append(func)

        if structure and structure_name:
            schema = ResponseJsonSchema(structure_name, structure, chat_log.llm_api)
            data.response_format = schema
            if "bigmodel.cn" in self.entry.get_config(CONF_BASE):
                # https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8#body-response-format
                data.response_format = Dict(type="json_object")
                data.messages.append(ChatMessage(
                    role="system",
                    content=(
                        "Please ensure that the response is in JSON schema:"
                        f"{json.dumps(schema.schema)}"
                    ),
                ))

        for _iteration in range(MAX_TOOL_ITERATIONS):
            result = await self.async_chat_completions(**data)
            if not result.message:
                continue
            data.messages.extend(
                [
                    msg
                    async for content in chat_log.async_add_delta_content_stream(
                        self.entity_id, result.message.to_conversation_content_delta()
                    )
                    if (msg := ChatMessage.from_conversation_content(content))
                ]
            )
            if not chat_log.unresponded_tool_results:
                break

    async def async_chat_completions(self, messages, **kwargs):
        model = kwargs.pop("model", None) or self.model
        data = ChatCompletions(model=model, messages=messages, **kwargs)
        try:
            result = await self.entry.async_chat_completions(data)
        except Exception as err:
            LOGGER.exception('chat_completions error: %s', data, exc_info=True)
            raise HomeAssistantError(f"Error talking to API: {err}") from err
        LOGGER.debug('chat_completions req: %s', data)
        if result.error:
            raise HomeAssistantError(f"Error talking to API: {result.error}")
        if not result.message:
            LOGGER.warning('chat_completions response has no message: %s', result)
        else:
            LOGGER.debug('chat_completions rsp: %s', result.message)
        return result
