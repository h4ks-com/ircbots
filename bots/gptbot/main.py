import json
import logging
import os

import requests
from dotenv import load_dotenv
from IrcBot.bot import IrcBot, utils

load_dotenv()

HOST = os.getenv("IRC_HOST")
assert HOST, "IRC_HOST is required"
SSL = os.getenv("IRC_SSL") == "true"
PORT = int(os.getenv("IRC_PORT") or 6667)
NICK = os.getenv("NICK") or "_bqbot"
PASSWORD = os.getenv("PASSWORD") or ""
CHANNELS = json.loads(os.getenv("CHANNELS") or "[]")

headers = {
    "accept": "application/json",
    "Content-Type": "application/json",
}

# TODO: make the command itself the model or provider argument like the old gpt bot did


@utils.arg_command("echo")
def echo(args, message):
    json_data = {
        "messages": [
            {
                "role": "user",
                "content": " ".join(utils.m2list(args)),
            },
        ],
    }
    request = requests.post(
        "https://g4f.cloud.mattf.one/api/completions",
        headers=headers,
        json=json_data,
    ).json()
    request["completion"]
    return request["completion"]


async def onConnect(bot: IrcBot):
    await bot.join("#bots")


if __name__ == "__main__":
    utils.setLogging(logging.INFO)
    bot = IrcBot(
        HOST, nick=NICK, channels=CHANNELS, password=PASSWORD, use_ssl=SSL, port=PORT
    )
    bot.runWithCallback(onConnect)
