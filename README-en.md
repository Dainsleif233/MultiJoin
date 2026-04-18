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

Then start Velocity and point the Mojang session server to MultiJoin:

```bash
java -Dmojang.sessionserver=http://127.0.0.1:2268/hasJoined -jar velocity.jar
```

Velocity must run with `online-mode` enabled. If MultiJoin and Velocity are not on the same machine, replace `127.0.0.1` with the address of the MultiJoin host and make sure the network and firewall allow access to port `2268`.

## Binding

The binding feature maps one MultiJoin profile to another existing profile. A common use case is making the same player appear as the same UUID on backend servers when they log in through different entries.

Install [MultiJoinPlugin](https://github.com/Dainsleif233/MultiJoinPlugin) on the Velocity proxy to use the binding feature. Set a strong `key` in `config.toml` and make sure only trusted plugins or services can access MultiJoin.

## Common Cases And Solutions

### Players Get Kicked Or Chat Session Validation Fails

Try these solutions:

- Install [ChatSessionBlocker](https://github.com/Dainsleif233/ChatSessionBlocker) on backend servers.
- Ask clients to install [No Chat Reports](https://modrinth.com/mod/no-chat-reports).
- Install [authlib-injector](https://github.com/yushijinhun/authlib-injector) on all backend servers and configure any valid Yggdrasil API.

### Skins From Other Entries Are Not Visible

Try these solutions:

- Install [JustEnoughSkins](https://github.com/Dainsleif233/JustEnoughSkins) on backend servers.
- Ask clients to install [CustomSkinLoader](https://modrinth.com/mod/customskinloader) and configure it correctly.

### Adding Offline Players

MultiJoin itself is designed for the Yggdrasil `hasJoined` flow and theoretically does not directly support normal offline players. A workable alternative is to deploy an independent skin server that allows players to register themselves, such as [Blessing Skin Server](https://github.com/bs-community/blessing-skin-server), and then add it to `config.toml` as a Yggdrasil entry.

You can use [skin-docker](https://github.com/Dainsleif233/skin-docker) to quickly deploy the related services.

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
- Whether the Velocity startup argument points to `/hasJoined`.

### Configuration Loading Fails

Check:

- Whether `config.toml` exists in the project root.
- Whether there is at least one `[[entries]]`.
- Whether every entry has a non-empty and unique `id`.
- Whether every entry has a non-empty `api` and a `format` containing `{name}`.
- Whether `alwaysFormat` is a boolean and `tokenExpiresIn` is a positive integer.

## Notes

- `profiles.csv` is runtime data and should be backed up regularly.
- Do not expose MultiJoin directly to the public internet. At minimum, use a firewall so only Velocity can access it.
- The binding feature depends on `key`. Set a strong secret and avoid leaking it.
- When multiple entries return the same player name, MultiJoin may rename players to satisfy Minecraft limits.
- When multiple entries return the same UUID, later entries will be mapped to new local profiles.
- This project only handles login profile aggregation. It does not replace a full permission, skin, chat-signature, or account system.
