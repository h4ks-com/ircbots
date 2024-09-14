import json
import os
from collections import deque
from typing import Deque, Literal, TypedDict

import requests
from dotenv import load_dotenv
from ircbot import IrcBot
from ircbot.message import Message

load_dotenv()

HOST = os.getenv("IRC_HOST")
assert HOST, "IRC_HOST is required"
SSL = os.getenv("IRC_SSL") == "true"
PORT = int(os.getenv("IRC_PORT") or 6667)
NICK = os.getenv("NICK") or "_bqbot"
PASSWORD = os.getenv("PASSWORD") or ""
CHANNELS = json.loads(os.getenv("CHANNELS") or "[]")
MAX_HISTORY = int(os.getenv("MAX_HISTORY") or 100)


class ApiMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class BotMessage(Message):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.role = "assistant"


class MyBot(IrcBot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.history: dict[str, Deque[Message]] = {}

    def add_to_history(self, message: Message):
        channel = message.channel
        if channel not in self.history:
            self.history[channel] = deque(maxlen=MAX_HISTORY)
        self.history[channel].append(message)

    @staticmethod
    def _fmt_message(message: Message) -> ApiMessage:
        role = "assistant" if isinstance(message, BotMessage) else "user"
        return {"role": role, "content": f"{message.sender_nick}: {message.text}"}

    def get_history(self, channel: str) -> list[ApiMessage]:
        return [MyBot._fmt_message(msg) for msg in self.history.get(channel, [])]


bot = MyBot(
    HOST,
    nick=NICK,
    channels=CHANNELS,
    password=PASSWORD,
    use_ssl=SSL,
    port=PORT,
)


@bot.regex_cmd_with_message(".*")
def add_to_history(args, message: Message):
    bot.add_to_history(message)


@bot.regex_cmd_with_message(rf"^\s*{NICK}:? ")
def ai_response(args, message: Message):
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
    }
    json_data = {
        "messages": bot.get_history(message.channel),
    }
    response = requests.post(
        "https://g4f.cloud.mattf.one/api/completions",
        headers=headers,
        json=json_data,
    )
    if not response.ok:
        return response.text
    response.raise_for_status()

    completion = response.json()["completion"]
    bot.add_to_history(BotMessage(completion, message.channel))
    return f"{message.sender_nick}: {completion}"


async def on_connect():
    await bot.join("#bots")


if __name__ == "__main__":
    bot.run_with_callback(on_connect)
