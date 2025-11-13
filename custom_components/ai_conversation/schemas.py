import json
from homeassistant.helpers import llm
from homeassistant.components import conversation
from voluptuous_openapi import convert

from .const import LOGGER


class Dict(dict):
    def __getattr__(self, item):
        return self.get(item)

    def __setattr__(self, key, value):
        self[key] = Dict(value) if isinstance(value, dict) else value

class ChatCompletions(Dict):
    @property
    def messages(self):
        return self.setdefault("messages", [])

    @property
    def tools(self):
        return self.setdefault("tools", [])

class ChatMessage(Dict):
    def __init__(self, content, role="user", **kwargs):
        if isinstance(content, str):
            content = content.lstrip()
        super().__init__(role=role, content=content, **kwargs)

    @staticmethod
    def from_conversation_content(content: conversation.Content):
        if isinstance(content, conversation.ToolResultContent):
            return ChatMessage(
                role="tool",
                content=json.dumps(content.tool_result),
                tool_call_id=content.tool_call_id,
            )

        role = content.role
        if role == "system" and content.content:
            return ChatMessage(role=role, content=content.content)
        if role == "user" and content.content:
            return ChatMessage(role=role, content=content.content)
        if role == "assistant":
            param = ChatMessage(role=role, content=content.content)
            if isinstance(content, conversation.AssistantContent) and content.tool_calls:
                param.tool_calls = [
                    Dict(
                        type="function",
                        id=tool_call.id,
                        function=Dict(arguments=json.dumps(tool_call.tool_args), name=tool_call.tool_name),
                    )
                    for tool_call in content.tool_calls
                ]
            return param
        return None

    async def to_conversation_content_delta(self):
        data = {
            "role": self.role,
            "content": self.content,
        }
        if self.tool_calls:
            data["tool_calls"] = [
                llm.ToolInput(
                    id=tool_call["id"],
                    tool_name=tool_call["function"]["name"],
                    tool_args=json.loads(tool_call["function"]["arguments"]),
                )
                for tool_call in self.tool_calls
            ]
        yield data


class ChatMessageContent(Dict):
    def __init__(self, text=None, image_url=None, video_url=None, file_url=None):
        if text is not None:
            super().__init__(type="text", text=text)
        elif image_url is not None:
            super().__init__(type="image_url", image_url=Dict(url=image_url))
        elif video_url is not None:
            super().__init__(type="video_url", video_url=Dict(url=video_url))
        elif file_url is not None:
            super().__init__(type="file_url", file_url=Dict(url=file_url))

class ChatTool(Dict):
    @staticmethod
    def from_hass_llm_tool(tool: llm.Tool, custom_serializer=None):
        func = Dict(
            name=tool.name,
            parameters=convert(tool.parameters, custom_serializer=custom_serializer),
        )
        if tool.description:
            func.description = tool.description
        return ChatTool(type="function", function=func)

class ResponseJsonSchema(Dict):
    def __init__(self, name, schema, llm_api=None):
        super().__init__(name=name, strict=True)
        self.schema = convert(
            schema,
            custom_serializer=llm_api.custom_serializer if llm_api else llm.selector_serializer,
        )
        self._adjust_schema(self.schema)

    def _adjust_schema(self, schema: dict):
        if schema["type"] == "object":
            if "properties" not in schema:
                return
            if "required" not in schema:
                schema["required"] = []
            for prop, prop_info in schema["properties"].items():
                self._adjust_schema(prop_info)
                if prop not in schema["required"]:
                    prop_info["type"] = [prop_info["type"], "null"]
                    schema["required"].append(prop)
        elif schema["type"] == "array":
            if "items" not in schema:
                return
            self._adjust_schema(schema["items"])

class ChatCompletionsResult(Dict):
    response = None

    def to_dict(self):
        data = self.copy()
        data.pop("response", None)
        return data

    @property
    def choices(self):
        choices = self.get("choices", [])
        for choice in choices:
            message = choice.get("message")
            if not message:
                 continue
            elif "content" in message:
                choice["message"] = ChatMessage(**message)
            else:
                LOGGER.info("Unknown message: %s", message)
        return choices

    @property
    def message(self):
        for choice in self.choices:
            if "message" in choice:
                return choice["message"]
        return None
