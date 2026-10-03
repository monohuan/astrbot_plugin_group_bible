# astrbot_plugin_group_bible

群聊消息收录与随机爆典插件，适配 AstrBot 4.28.x 与 aiocqhttp/NapCat。

## 指令

- 引用消息后 `/入典` 或 `/gbible add`
- `/爆典` 或 `/gbible random`
- `/删典 #ID` 或 `/gbible del #ID`
- `/群圣经` 或 `/gbible`
- `/群圣经帮助` 或 `/gbible help`
- `/群圣经列表 [页码]` 或 `/gbible list [页码]`
- `/群圣经 #ID` 或 `/gbible #ID`

入典权限等级：`0` 仅 AstrBot 管理员，`1` 增加群主和群管理员，`2` 所有成员。

戳一戳机器人时，如果当前群已有圣经，插件随机发送一条并终止默认 LLM 对话；如果当前群没有圣经，事件继续交给 AstrBot 正常对话。

## LLM Poke 兼容

兼容 `astrbot_plugin_poke_to_llm`（以及默认优先级的同类 LLM Poke 插件）：群圣经监听优先级为 `10000`。当前群有条目时由群圣经先行接管并停止传播，确保不会同时爆典和生成 LLM 回复；当前群没有条目时不修改 `should_call_llm`、不停止事件，完整交给 LLM Poke 插件处理。
