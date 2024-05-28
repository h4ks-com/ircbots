# Chat Apropo Bots
Monorepo for all bots in [Chat Apropo](irc.chat.dot.org.es).

## Bots
Bots go inside [bots](bots) directory. Each bot must have:

- A directory with the name of the bot.
- A `main.py` file with the entry point of the bot.
- A `requirements.txt` file with the dependencies of the bot.
- Optional `requirements-dev.txt` file with the development dependencies of the bot. You can build dev mode with `./scripts/build_envs.sh dev`.
- Optional `README.md` file with the description of the bot.
- Optional `.env.example` file with the environment variables that the bot needs.


## Environment variables in docker image
The global environment variables are passed as base64 encoded JSON in the format of the `config.example.json` into the docker file. To build the example run the 
pre-commit or:

```bash
python scripts/gen_config.py > config.example.json
```

At the docker runtime each entry will be loaded into the bots environment variables.

The docker image runtime expects the contends of this file to be base64 encoded JSON in the `CD_ENV_VARS` environment variable.
