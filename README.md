# 共界

多角色共享世界的本地沉浸式角色扮演原型。

## 启动

```bash
./start.sh
```

打开 <http://127.0.0.1:8768>，在右上角连接 OpenAI-compatible 或 Anthropic Messages API。

## 当前闭环

- 从世界书架进入一个持续世界：先看见此刻的公开局面，再从依次浮现的角色卡中选择要去见的人。
- 角色首次进入时根据世界开场、相遇牵引与自己的认知生成一次开场，并保存为对话的第一条消息。
- 每名角色拥有独立人格、欲望指纹、关系与知识边界。
- 回复后由独立状态裁判提取场景、关系、事件与知情范围，通过校验的事实自动写入世界。
- 亲历者立即获得认知；公开变化经“同步角色认知”后分别进入其他角色的认知投影。
- 角色回复下方会显示轻量的世界记录，世界驾驶舱只展示公开大事与角色表层状态。
- API Key 独立保存在 `data/secrets.json`，不进入 SQLite 或 Git。

Provider 层沿用了 Aru `codex/full-shell-material-recipes` 分支的核心边界：provider-neutral request context、协议 adapter、SSE canonical event、cumulative stream snapshot 与 normalized error。

## 开发入口

- 产品语义与模块边界以 [`docs/CONCEPT.md`](docs/CONCEPT.md) 为准。
- 后端入口是 `gongjie.app:app`，前端仍由 `static/` 提供。
- 关键验证运行 `.venv/bin/python -m unittest discover -s tests -v`。
