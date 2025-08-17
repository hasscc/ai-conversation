# 🤖 AI Conversation Agent

## Install

#### Method 1: [HACS](https://my.home-assistant.io/redirect/hacs_repository/?category=integration&owner=hasscc&repository=ai-conversation)

#### Method 2: Manually installation via Samba / SFTP
> Download and copy `custom_components/ai_conversation` folder to `custom_components` folder in your HomeAssistant config folder

#### Method 3: Onekey shell via SSH / Terminal & SSH add-on
```shell
wget -O - https://get.hacs.vip | DOMAIN=ai_conversation REPO_PATH=hasscc/ai-conversation ARCHIVE_TAG=main bash -
```

#### Method 4: shell_command service
1. Copy this code to file `configuration.yaml`
    ```yaml
    shell_command:
      update_ai_conversation: |-
        wget -O - https://get.hacs.vip | DOMAIN=ai_conversation REPO_PATH=hasscc/ai-conversation ARCHIVE_TAG=main bash -
    ```
2. Restart HA core
3. Call this [`action: shell_command.update_ai_conversation`](https://my.home-assistant.io/redirect/developer_call_service/?service=shell_command.update_xiaomi_miot) in Developer Tools
2. Restart HA core again


## Config

[![Config AI Conversation](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ai_conversation)


## Actions

### Explain media
```yaml
action: ai_conversation.explain_media
data:
  entity_id: conversation.agent_glm_4_1v_thinking_flash
  prompt: Explain this video
  video: https://ha.your.domain/media/local/camera.mp4
  tags:
    - Car
    - Delivery Person

# Response
message: The video captures a street scene where multiple cars pass by on the road.
tags:
   - Car
tags_string: "#Car"
usage:
   completion_tokens: 180
   prompt_tokens: 3683
   total_tokens: 3863
```


## Links

- [智谱AI免费模型](https://www.bigmodel.cn/invite?icode=EwilDKx13%2FhyODIyL%2BKabHHEaazDlIZGj9HxftzTbt4%3D)
- [New API](https://github.com/Calcium-Ion/new-api)
