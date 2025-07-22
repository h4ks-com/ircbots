import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from multiprocessing import Process

from dotenv import load_dotenv
from ircbot import IrcBot, utils
from ircbot.format import (
    format_line_breaks,
    irc_sanitize_nick,
    markdown_to_irc,
    truncate,
)
from ircbot.message import Message
from lib import ClientWrapper, get_token
from PyCharacterAI.exceptions import SessionClosedError
from PyCharacterAI.types import CharacterShort


def remove_surrounding_quotes(text: str) -> str:
    text = text.strip()
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    return text


load_dotenv()

HOST = os.getenv("IRC_HOST")
assert HOST, "IRC_HOST is required"
PORT = int(os.getenv("IRC_PORT") or 6667)
SSL = os.getenv("IRC_SSL") == "true"
NICK = os.getenv("NICK") or "caibot"
PASSWORD = os.getenv("PASSWORD") or None
CHANNELS = json.loads(remove_surrounding_quotes(os.getenv("CHANNELS") or "[]"))
CHAR = os.getenv("CHAR")
CHAT_ID = os.getenv("CHAT_ID")

MAX_SEARCH_RESULTS = 3
MAX_CHARACTERS = 3

assert CHAR, "CHAR is required"
assert CHAT_ID, "CHAT_ID is required"


@dataclass
class UserData:
    search_results: list[CharacterShort]
    shown_results: list[CharacterShort]


@dataclass
class ChannelData:
    users: dict[str, UserData] = field(default_factory=dict)
    children: dict[str, Process] = field(default_factory=dict)


@dataclass
class BotData:
    token: str
    client: ClientWrapper
    channels: dict[str, ChannelData] = field(default_factory=dict)


class CustomBot(IrcBot):
    _data: BotData

    @property
    def data(self) -> BotData:
        return self._data

    @data.setter
    def data(self, value: BotData):
        self._data = value


bot = CustomBot(HOST, PORT, NICK, CHANNELS, PASSWORD, use_ssl=SSL)
utils.set_loglevel(logging.INFO)
bot.set_prefix("+")


async def format_response(
    bot: CustomBot, text: str, nick: str | None = None
) -> list[str]:
    me = await bot.data.client.client.account.fetch_me()
    my_nick = me.username
    name = "everyone" if nick is None else nick
    text = re.sub(rf"\b{re.escape(my_nick)}\b", name, text, flags=re.IGNORECASE)
    lines = format_line_breaks(markdown_to_irc(text))
    return [ln for ln in lines if ln]


def install_conversation_hooks(
    mybot: CustomBot, nick: str = NICK, char: str = CHAR, chat_id: str = CHAT_ID
):
    @mybot.regex_cmd_with_message(
        rf"(?i)^((?:.*\s)?{nick}([\s|,|\.|\;|\?|!|:]*)(?:\s.*)?)$", False
    )
    async def mention(args: re.Match, message: Message):
        if message.sender_nick == NICK:
            return

        text = args[1].strip()
        for _ in range(3):
            try:
                answer = await mybot.data.client.client.chat.send_message(
                    char, chat_id, text
                )
                response = answer.get_primary_candidate().text
                break
            except SessionClosedError:
                logging.error(
                    f"Session closed for {nick}, trying to refresh client ..."
                )
                await mybot.data.client.refresh_client()
                continue
        else:
            response = "Sorry, I couldn't process your request at the moment."

        await mybot.reply(
            message,
            await format_response(mybot, response, message.sender_nick),
        )


def add_character_to_channel(token: str, channel: str, nick: str, char: CharacterShort):
    def create_bot() -> CustomBot:
        new_bot = CustomBot(HOST, PORT, nick, channel, use_ssl=SSL)
        new_bot.data = BotData(channels={}, token=token, client=ClientWrapper(token))

        @new_bot.regex_cmd_with_message("^help .*")
        def no_help(args: re.Match, message: Message):
            pass

        return new_bot

    async def get_char():
        await new_bot.data.client.refresh_client()
        chat, greeting_message = await new_bot.data.client.new_chat(char.character_id)
        text = greeting_message.get_primary_candidate().text if greeting_message else ""
        await bot.sleep(0.5)
        logging.info(f"Got response for {nick}")
        install_conversation_hooks(
            new_bot, nick=new_bot.nick, char=char.character_id, chat_id=chat.chat_id
        )
        new_bot.install_hooks()
        await new_bot.join(channel)
        await new_bot.send_message(await format_response(new_bot, text), channel)

    for _ in range(5):
        try:
            new_bot = create_bot()
            new_bot.run_with_callback(get_char)
        except ConnectionError:
            logging.error(f"Connection error for {nick}, trying again ...")
            time.sleep(2)

    logging.error(f"Connection error for {nick}, giving up.")


def get_search_results_lines(
    message: Message, search_results: list[CharacterShort]
) -> list[str]:
    lines = []
    user_data = bot.data.channels[message.channel].users.get(message.sender_nick)
    if not user_data or len(search_results) == 0:
        return ["No search results available"]
    i = 0
    for i, char in enumerate(search_results):
        title = char.title.replace("{{user}}", message.sender_nick)
        greeting = char.greeting.replace("{{user}}", message.sender_nick)
        lines.append(
            truncate(
                f"{i+1}) \x02{char.name}\x02 - {markdown_to_irc(title)} :: {markdown_to_irc(greeting)}",
            )
        )
        user_data.shown_results.append(char)
        if i == MAX_SEARCH_RESULTS - 1:
            break
    user_data.search_results = search_results[i + 1 :]
    return lines


@bot.arg_command("search", "Search for a character", "search <query>")
async def search(args: re.Match, message: Message):
    client = bot.data.client
    query = "+".join(utils.m2list(args))
    search_results = await client.search_characters(query)
    bot.data.channels[message.channel].users[message.sender_nick] = UserData(
        search_results=search_results, shown_results=[]
    )
    await bot.reply(message, get_search_results_lines(message, search_results))


@bot.arg_command("more", "Get more search results", "more")
async def more(args: re.Match, message: Message):
    user_data = bot.data.channels[message.channel].users.get(message.sender_nick)
    if not user_data:
        await bot.reply(message, "No search results available")
        return
    search_results = (
        bot.data.channels[message.channel].users[message.sender_nick].search_results
    )
    if not search_results or len(search_results) == 0:
        await bot.reply(message, "No search results available")
        return
    await bot.reply(message, get_search_results_lines(message, search_results))


@bot.arg_command("add", "Add a character to the conversation", "add <number>")
async def add(args: re.Match, message: Message):
    user_data = bot.data.channels[message.channel].users.get(message.sender_nick)
    if user_data is None:
        await bot.reply(message, "No search results available")
        return

    if not user_data.shown_results:
        await bot.reply(message, "No search results available")
        return

    if len(bot.data.channels[message.channel].children) >= MAX_CHARACTERS:
        await bot.reply(message, "Maximum number of characters reached")
        return

    if not args[1].isdigit():
        await bot.reply(message, "Invalid number")
        return

    number = int(args[1])
    if number < 1 or number - 1 > len(user_data.shown_results):
        await bot.reply(message, "Invalid number")
        return

    char = user_data.shown_results[number - 1]
    nick = irc_sanitize_nick(char.name)
    if nick in bot.data.channels[message.channel].children:
        await bot.reply(
            message, f"Character '{nick}' is already in use. Delete it first."
        )
        return

    process = Process(
        target=add_character_to_channel,
        args=(bot.data.token, message.channel, nick, char),
        daemon=True,
    )
    process.start()
    bot.data.channels[message.channel].children[nick] = process


async def kill_process(process: Process):
    process.terminate()
    await asyncio.sleep(0.5)
    if process.is_alive() and process.pid:
        os.kill(process.pid, 9)


@bot.arg_command(
    "delete", "Remove a character from the conversation", "del <nick>", alias="remove"
)
async def delete(args: re.Match, message: Message):
    if args[1] not in bot.data.channels[message.channel].children:
        await bot.reply(message, f"Character '{args[1]}' not found")
        return

    process: Process = bot.data.channels[message.channel].children.pop(args[1])
    await kill_process(process)


@bot.arg_command("restart", "Remove all characters from the conversation", "wipeout")
async def wipeout(args: re.Match, message: Message):
    for nick, process in bot.data.channels[message.channel].children.items():
        await kill_process(process)
    bot.data.channels[message.channel].children = {}
    await bot.reply(message, "All characters removed")


@bot.arg_command("list", "List all characters in the conversation", "list")
async def list_characters(args: re.Match, message: Message):
    characters = list(bot.data.channels[message.channel].children.keys())
    await bot.reply(message, f"Managed nicks: {', '.join(characters)}")


async def on_connect():
    token = await get_token()
    bot.data = BotData(channels={}, token=token, client=ClientWrapper(token))
    await bot.data.client.refresh_client()

    for channel in CHANNELS:
        await bot.join(channel)
        bot.data.channels[channel] = ChannelData()

    await bot.send_message("Hello everyone !!!")


if __name__ == "__main__":
    install_conversation_hooks(bot)
    bot.run_with_callback(on_connect)
