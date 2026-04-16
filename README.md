## How to Use ?

1. Edit `config.toml` to set your entries.
2. Turn on the `online-mode` in your Velocity config.

**Then**

```bash
pip install -r requirements.txt
python main.py
java -Dmojang.sessionserver=http://127.0.0.1:2268/hasJoined -jar velocity.jar
```

## Have been kicked ?

Try [ChatSessionBlocker](https://github.com/Dainsleif233/ChatSessionBlocker).

**Or**

Use [No Chat Reports](https://modrinth.com/mod/no-chat-reports) on Clients.

**Also could**

Install [authlib-injector](https://github.com/yushijinhun/authlib-injector) on all servers and configure any valid yggdrasil api.

## Want to see the skins of players from other entrances ?

Use [JustEnoughSkins](https://github.com/Dainsleif233/JustEnoughSkins).

**Or**

Use [CustomSkinLoader](https://modrinth.com/mod/customskinloader) on Clients.

## Want to add offline players ?

Theoretically not supported.

**But**

You can choose to deploy a separate [Blessing Skin Server](https://github.com/bs-community/blessing-skin-server) for offline players that allows them to register freely.

Use [skin-docker](https://github.com/Dainsleif233/skin-docker) to deploy the server.
