# 共界

多角色共享世界的本地沉浸式角色扮演原型。

## 启动

```bash
./start.sh
```

打开 <http://127.0.0.1:8768>，在右上角连接 OpenAI-compatible 或 Anthropic Messages API。

## 当前闭环

- 每名角色拥有独立人格、欲望指纹、关系与知识边界。
- 所有角色共用世界、地点、公开正史与时间线。
- 回复后由独立状态裁判提取场景、关系、事件与知情范围。
- 支线事件只活在对应分支；提升为正史后才进入其他角色的世界上下文。
- API Key 独立保存在 `data/secrets.json`，不进入 SQLite 或 Git。

Provider 层沿用了 Aru `codex/full-shell-material-recipes` 分支的核心边界：provider-neutral request context、协议 adapter、SSE canonical event、cumulative stream snapshot 与 normalized error。
