# MultiJoin

MultiJoin is a lightweight `hasJoined` aggregation service for Minecraft Velocity proxies. It lets one entry point accept multiple Yggdrasil login sources while keeping player identities as stable as possible.

[中文](README.md)

## Features

- Aggregates multiple Yggdrasil `hasJoined` APIs.
- Automatically handles UUID conflicts when different entries return the same UUID.
- Automatically handles player name conflicts and renames players with the configured entry format when needed.
- Can force all entries to use a unified name format with `alwaysFormat`.
- Adds `multijoin` metadata to profile properties so other plugins can identify the source.
- Provides a binding feature that can bind one entry's profile to another existing profile.
- Supports per-entry UUID whitelists to restrict which players can log in through a given entry.

## Installation

Python 3.8 or newer is required.

```bash
pip install -r requirements.txt
```

Copy the example configuration:

```bash
cp config.example.toml config.toml
```

## Configuration

Edit `config.toml`:

```toml
alwaysFormat = false
key = ""
tokenExpiresIn = 600

[[entries]]
id = "mojang"
api = "https://sessionserver.mojang.com/session/minecraft/hasJoined"
format = "{name}_{entry}"
```

Options:

- `alwaysFormat`: whether to always apply `format`. When set to `false`, names are only changed when a player name conflict occurs.
- `key`: secret key for the binding feature. Binding is unavailable when it is empty. Use a sufficiently long random string.
- `tokenExpiresIn`: binding token lifetime in seconds. The example value is `600`.
- `[[entries]]`: one login entry. Multiple entries can be configured.
- `id`: entry ID. It must be unique in this configuration.
- `api`: the full Yggdrasil `hasJoined` URL. Do not include query parameters.
- `format`: player name format. It must contain `{name}` and may use `{entry}`.

Note: Minecraft player names are limited to 16 characters. MultiJoin tries to avoid conflicts by truncating and incrementing names, but it may still fail to generate a valid name if `format` itself is too long. Keep entry suffixes short, such as `{name}_m`, or use `{name}_{entry}` with short `id` values.

## Startup

Start MultiJoin first:

```bash
python main.py
```

It listens on:

```text
0.0.0.0:2268
```

On startup, the whitelist status is printed. If `whitelist.txt` does not exist or is empty, all entries are allowed.

Then start Velocity and point the Mojang session server to MultiJoin:

```bash
java -Dmojang.sessionserver=http://127.0.0.1:2268/hasJoined -jar velocity.jar
```

Velocity must run with `online-mode` enabled. If MultiJoin and Velocity are not on the same machine, replace `127.0.0.1` with the address of the MultiJoin host and make sure the network and firewall allow access to port `2268`.

## Binding

The binding feature maps one MultiJoin profile to another existing profile. A common use case is making the same player appear as the same UUID on backend servers when they log in through different entries.

Install [MultiJoinPlugin](https://modrinth.com/plugin/multijoinplugin) on the Velocity proxy to use the binding feature. Set a strong `key` in `config.toml` and make sure only trusted plugins or services can access MultiJoin.

## Whitelist

The whitelist feature restricts which UUIDs can log in through a given entry. The configuration file is `whitelist.txt` in the project root. It uses INI-style sections, where each section header corresponds to an entry `id` in `config.toml`:

```text
[union]  # union entry whitelist
4845a2d91444325caa4e772f16e04762  # player A
588a5182b704380b87cf295b0d1e39c6  # player B

[mojang]
a94c19ea783b41eb87921e5f256be9dd
```

- One UUID per line. UUIDs may include or omit hyphens and are case-insensitive.
- Everything after `#` is treated as a comment, either at the start or middle of a line. Blank lines are ignored.
- If an entry has no corresponding section, that entry is not restricted (all players allowed).
- If an entry has a corresponding section but it is empty, that entry rejects everyone.
- **Hot reading is supported**: after modifying `whitelist.txt`, MultiJoin automatically reloads it on the next request without restarting.

When an entry returns a 200 response but the returned UUID is not in that entry's whitelist, MultiJoin skips the response and continues trying other entries. A `[DENY]` log is printed for rejected logins.

See `whitelist.example.txt` for a template:

```bash
cp whitelist.example.txt whitelist.txt
```

## API

MultiJoin provides an API for querying a player's original profile in game.

```java
GameProfile.Property mjProperty = player.getGameProfileProperties().stream().filter(p -> p.getName().equals("multijoin")).findFirst().orElse(null);
if (mjProperty != null) {
    JsonObject mjData = JsonParser.parseString(mjProperty.getValue()).getAsJsonObject();
}
```

`mjData` contains the current logged-in player's entry data in MultiJoin. It contains these fields:

- profile
- entry
- uuid
- name
- bind

[MultiJoinPlugin](https://modrinth.com/plugin/multijoinplugin) is an example.

## Common Cases And Solutions

### Players Get Kicked Or Chat Session Validation Fails

Try these solutions:

- Install [ChatSessionBlocker](https://modrinth.com/plugin/chatsessionblocker) on Velocity.
- Ask clients to install [No Chat Reports](https://modrinth.com/mod/no-chat-reports).
- Install [authlib-injector](https://github.com/yushijinhun/authlib-injector) on all backend servers and configure any valid Yggdrasil API.

### Skins From Other Entries Are Not Visible

Try these solutions:

- Install [JustEnoughSkins](https://modrinth.com/plugin/justenoughskins) on Velocity.
- Ask clients to install [CustomSkinLoader](https://modrinth.com/mod/customskinloader) and configure it correctly.

### Adding Offline Players

MultiJoin itself is designed for the Yggdrasil `hasJoined` flow and theoretically does not directly support normal offline players. A workable alternative is to deploy an independent skin server that allows players to register themselves, such as [Blessing Skin Server](https://github.com/bs-community/blessing-skin-server), and then add it to `config.toml` as a Yggdrasil entry.

You can use [skin-docker](https://github.com/Dainsleif233/skin-docker) to quickly deploy the related services.

Or use a public Yggdrasil service, for example:

- [LittleSkin](https://littleskin.cn/): `https://littleskin.cn/api/yggdrasil/sessionserver/session/minecraft/hasJoined`
- [RedstoneSkin](https://mcskin.cn/): `https://mcskin.cn/api/yggdrasil/sessionserver/session/minecraft/hasJoined`
- [MUA User Center](https://skin.mualliance.ltd/): `https://skin.mualliance.ltd/api/yggdrasil/sessionserver/session/minecraft/hasJoined`
- [Ely.by](https://ely.by/): `https://account.ely.by/api/authlib-injector/sessionserver/session/minecraft/hasJoined`

### Duplicate Player Names Across Entries

When `alwaysFormat = false`, MultiJoin only renames players when conflicts occur. If you want the entry source to always be visible, set:

```toml
alwaysFormat = true
```

Then use a short format for each entry:

```toml
format = "{name}_m"
```

### Login Fails And The Console Shows `[MISS]`

This means none of the entries returned a valid profile. Check:

- Whether the player actually completed online/Yggdrasil login through one of the entries.
- Whether the `api` address is correct and reachable from the machine running MultiJoin.
- Whether the entry service is too slow. MultiJoin waits about 5 seconds for each request round.

### Configuration Loading Fails

Check:

- Whether `config.toml` exists in the project root.
- Whether there is at least one `[[entries]]`.
- Whether every entry has a non-empty and unique `id`.
- Whether every entry has a non-empty `api` and a `format` containing `{name}`.
- Whether `alwaysFormat` is a boolean and `tokenExpiresIn` is a positive integer.

## Notes

- **Do not manually edit `profiles.csv` while the service is running.** MultiJoin uses a WAL (write-ahead log) mechanism; the next checkpoint will overwrite external changes with in-memory data, causing your edits to be lost. To bulk-edit data, **stop the service, edit the CSV, delete `profiles.wal` in the same directory, then restart**. For normal operations, use the bind API.
- `profiles.csv` is runtime data and should be backed up regularly.
- Do not expose MultiJoin directly to the public internet. At minimum, use a firewall so only Velocity can access it.
- The binding feature depends on `key`. Set a strong secret and avoid leaking it.
- When multiple entries return the same player name, MultiJoin may rename players to satisfy Minecraft limits.
- When multiple entries return the same UUID, later entries will be mapped to new local profiles.
- This project only handles login profile aggregation. It does not replace a full permission, skin, chat-signature, or account system.
