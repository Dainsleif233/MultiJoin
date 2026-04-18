# MultiJoin

MultiJoin 是一个面向 Minecraft Velocity 代理端的轻量级 `hasJoined` 聚合服务，用于在同一个入口接入多个 Yggdrasil 登录源，并尽量保持玩家身份稳定。

[English](README-en.md)

## 功能概览

- 聚合多个 Yggdrasil `hasJoined` API。
- 自动处理不同入口返回相同 UUID 时的冲突。
- 自动处理玩家名冲突，必要时按入口格式重命名。
- 可通过 `alwaysFormat` 强制所有入口都使用统一命名格式。
- 向 profile properties 追加 `multijoin` 元数据，便于其他插件识别来源。
- 提供绑定功能，可将一个入口的 profile 绑定到另一个已存在 profile。

## 安装

需要 Python 3.8 或更高版本。

```bash
pip install -r requirements.txt
```

复制示例配置：

```bash
cp config.example.toml config.toml
```

## 配置

编辑 `config.toml`：

```toml
alwaysFormat = false
key = ""
tokenExpiresIn = 600

[[entries]]
id = "mojang"
api = "https://sessionserver.mojang.com/session/minecraft/hasJoined"
format = "{name}_{entry}"
```

字段说明：

- `alwaysFormat`：是否总是套用 `format`。为 `false` 时，仅在玩家名冲突时重命名。
- `key`：绑定功能密钥。为空时绑定功能不可用，建议设置为足够长的随机字符串。
- `tokenExpiresIn`：绑定 token 有效期，单位为秒，默认示例为 `600`。
- `[[entries]]`：一个登录入口。可以配置多个。
- `id`：入口 ID，只能在本配置中唯一。
- `api`：对应 Yggdrasil 的 `hasJoined` 完整地址，不要带查询参数。
- `format`：玩家名格式，必须包含 `{name}`，可使用 `{entry}`。

注意：Minecraft 玩家名最长 16 个字符。MultiJoin 会尝试截断和递增名称来避开冲突，但如果 `format` 本身过长，仍可能无法生成合法名称。建议保留较短的入口后缀，例如 `{name}_m` 或 `{name}_{entry}` 配合短 `id`。

## 启动

先启动 MultiJoin：

```bash
python main.py
```

默认监听：

```text
0.0.0.0:2268
```

再启动 Velocity，并将 Mojang session server 指向 MultiJoin：

```bash
java -Dmojang.sessionserver=http://127.0.0.1:2268/hasJoined -jar velocity.jar
```

Velocity 配置中需要启用 `online-mode`。如果 MultiJoin 和 Velocity 不在同一台机器，替换 `127.0.0.1` 为 MultiJoin 所在主机地址，并确保网络和防火墙允许访问 `2268` 端口。

## 绑定

绑定功能用于把一个 MultiJoin profile 指向另一个已存在 profile。典型用途是让同一个玩家从不同入口登录时，在后端服务器表现为同一个 UUID。

在 Velocity 代理端安装 [MultiJoinPlugin](https://github.com/Dainsleif233/MultiJoinPlugin) 以使用绑定功能。请在 `config.toml` 中设置强 `key`，并确保只有可信插件或服务可以访问 MultiJoin。

## 常见情况与解决方法

### 玩家被踢出或聊天会话校验失败

可以尝试以下方案：

- 在后端服务器安装 [ChatSessionBlocker](https://github.com/Dainsleif233/ChatSessionBlocker)。
- 让客户端安装 [No Chat Reports](https://modrinth.com/mod/no-chat-reports)。
- 在所有后端服务器安装 [authlib-injector](https://github.com/yushijinhun/authlib-injector)，并配置任意可用的 Yggdrasil API。

### 看不到其他入口玩家的皮肤

可以尝试以下方案：

- 在后端服务器安装 [JustEnoughSkins](https://github.com/Dainsleif233/JustEnoughSkins)。
- 让客户端安装 [CustomSkinLoader](https://modrinth.com/mod/customskinloader)，并正确配置。

### 想接入离线玩家

MultiJoin 本身面向 Yggdrasil `hasJoined` 流程，理论上不直接支持普通离线玩家。可行替代方案是部署一个允许玩家自行注册的独立皮肤站，例如 [Blessing Skin Server](https://github.com/bs-community/blessing-skin-server)，再将它作为一个 Yggdrasil 入口加入 `config.toml`。

可以使用 [skin-docker](https://github.com/Dainsleif233/skin-docker) 快速部署相关服务。

### 多入口玩家名重复

保持 `alwaysFormat = false` 时，MultiJoin 只在冲突时改名；如果你希望入口来源始终可见，可以设置：

```toml
alwaysFormat = true
```

并为每个入口设置短格式：

```toml
format = "{name}_m"
```

### 登录失败且控制台出现 `[MISS]`

这表示所有入口都没有返回有效 profile。检查：

- 玩家是否真的通过其中一个入口完成了正版/Yggdrasil 登录。
- `api` 地址是否正确，且能从 MultiJoin 所在机器访问。
- 入口服务是否响应过慢。MultiJoin 单次请求等待时间约为 5 秒。
- Velocity 启动参数是否指向了 `/hasJoined`。

### 配置读取失败

检查：

- 项目根目录是否存在 `config.toml`。
- 至少存在一个 `[[entries]]`。
- 每个入口都有非空且唯一的 `id`。
- 每个入口都有非空 `api` 和包含 `{name}` 的 `format`。
- `alwaysFormat` 是布尔值，`tokenExpiresIn` 是正整数。

## 注意事项

- `profiles.csv` 是运行时数据文件，应定期备份。
- 不建议把 MultiJoin 直接暴露到公网；至少应由防火墙限制只有 Velocity 可以访问。
- 绑定功能依赖 `key`，请设置强密钥并避免泄露。
- 多个入口返回同一个玩家名时，MultiJoin 可能会改变玩家名以满足 Minecraft 限制。
- 多个入口返回同一个 UUID 时，后续入口会被映射到新的本地 profile。
- 本项目只处理登录 profile 聚合，不替代完整权限、皮肤、聊天签名或账号系统。
