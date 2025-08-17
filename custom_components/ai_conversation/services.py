from homeassistant.core import ServiceCall, SupportsResponse

from .const import *

class ServiceManager:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass

    def setup_explain_media(self):
        from . import HassEntry
        async def service(call: ServiceCall):
            entity_ids = call.data.get(ATTR_ENTITY_ID)
            if not entity_ids:
                return {"error": "No entity id"}
            for entry in HassEntry.ALL.values():
                for entity_id, entity in entry.entities.items():
                    if entity_id not in entity_ids:
                        continue
                    return await entity.async_explain_media(**call.data)
            return {"error": "Unknown"}
        self.hass.services.async_register(
            DOMAIN, "explain_media", service,
            supports_response=SupportsResponse.OPTIONAL,
        )
