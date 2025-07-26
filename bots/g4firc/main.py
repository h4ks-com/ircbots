import asyncio
import json
import logging
import os
import re
import tempfile
from collections import deque
from functools import lru_cache
from typing import List

import requests
from cachetools import TTLCache
from dotenv import load_dotenv
from ircbot import IrcBot, Message, utils
from ircbot import message as message_module
from ircbot.client import MAX_MESSAGE_LEN, PersistentData
from ircbot.format import format_line_breaks, markdown_to_irc

load_dotenv()

NICK = os.environ["NICK"]
SERVER = os.environ["IRC_HOST"]
CHANNELS = json.loads(os.environ["CHANNELS"])
PORT = int(os.environ.get("IRC_PORT") or 6667)
PASSWORD = os.environ["PASSWORD"] if "PASSWORD" in os.environ else None
SSL = os.environ["IRC_SSL"] == "true"
DATABASE = os.environ.get("DATABASE") or "database.db"
MAX_CHATS_PER_USER = int(os.environ.get("MAX_CHATS_PER_USER") or 10)

API_BASE_URL = "https://g4f.h4ks.com"

COMMANDS = [
    (
        "list",
        "List all providers",
        "Lists available providers with their info like model and url. Same as providers",
    ),
    (
        "info",
        "Gets info about one specific provider",
        "Gets info about one specific provider. Usage: !info <provider>",
    ),
    (
        "gpt",
        "Use any provider/model that works, not necessarily gpt",
        "Use any provider that works, not necessarily gpt. Usage: !gpt <text> If provider is not specified, it will use the first available provider.",
    ),
    (
        "providers",
        "List all providers",
        "Lists available providers with their info like model and url. Same as list",
    ),
    (
        "clear",
        "Clears context",
        "Clears context for the user. Starts a fresh converstaion.",
    ),
    (
        "save",
        "Saves the context permanently",
        "Saves the context permanently. You can restore it later with !load. Usage: !save",
    ),
    (
        "load",
        "Loads the context permanently",
        "Loads the context permanently. You can use !history to see available chat histories to switch to. Usage: !load <chat_id>",
    ),
    (
        "history",
        "Lists all saved chat histories",
        "Lists all saved chat histories. Use !save or !load to manage them. Usage: !history",
    ),
    (
        "paste",
        "Pastes the context to pastebin",
        "Pastes all lines of the current context to ix.io. Usage: !paste",
    ),
]

model_map = {}
all_providers = {}


# Function to fetch providers and models from the API
def fetch_providers_and_models():
    global all_providers, model_map
    try:
        response = requests.get(f"{API_BASE_URL}/api/providers")
        response.raise_for_status()
        all_providers = response.json()

        # Build model map for quick lookup
        for provider_name, provider_data in all_providers.items():
            for model in provider_data.get("supported_models", []):
                model_map[model.lower()] = model

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching providers and models: {e}")
        # Fallback or exit if essential data cannot be fetched
        exit(1)


# Call the function to fetch data at startup
fetch_providers_and_models()


chats = PersistentData(DATABASE, "chats", ["nick", "chat", "headline"])
message_history = PersistentData(
    DATABASE, "messages", ["nick", "role", "chat", "message"]
)

assert SERVER, "SERVER is not set"
assert NICK, "NICK is not set"
assert CHANNELS, "CHANNELS is not set"
assert PORT, "PORT is not set"
bot = (
    IrcBot(
        SERVER,
        nick=NICK,
        port=PORT,
        use_ssl=SSL,
        password=PASSWORD or "",
        tables=[chats, message_history],
    )
    .set_prefix("!")
    .set_help_header(
        "GPT bot! Generate text using gtp4free. Context is saved for each user individually and between different providers. Check my DM!"
    )
    .set_help_on_private(True)
    .set_max_arguments(400)
)


@lru_cache(maxsize=512)
def get_user_context(nick: str) -> deque[dict]:
    """Get the user context."""
    return deque([], maxlen=1024)


def list_chats(nick: str) -> list[str]:
    """List all chats."""
    return [
        f'{nick}: {chat["chat"]} -> {chat["headline"]}'
        for chat in chats.data
        if chat["nick"] == nick
    ]


def load_chat_history(nick: str, chat_id: int):
    """Load the chat history and replace the cache."""
    for chat in chats.data:
        if chat["nick"] == nick and chat["chat"] == chat_id:
            break
    else:
        raise KeyError(f"Chat {chat_id} not found for user {nick}")
    history = [
        {"role": message["role"], "content": message["message"]}
        for message in message_history.data
        if message["nick"] == nick and message["chat"] == chat_id
    ]
    cache = get_user_context(nick)
    cache.clear()
    cache.extend(history)


def del_chat_history(nick: str, chat_id: int):
    """Delete the chat history and messages."""
    for chat in chats.data:
        if chat["nick"] == nick and chat["chat"] == chat_id:
            chats.pop(chat["id"])
            break
    else:
        raise KeyError(f"Chat {chat_id} not found for user {nick}")
    ids = []
    for message in message_history.data:
        if message["nick"] == nick and message["chat"] == chat_id:
            ids.append(message["id"])
    for id in ids:
        message_history.pop(id)


def save_chat_history(nick: str):
    """Save the chat history to the database.

    Make sure the maximum is respected and the oldest is dropped
    """
    chat_ids = []
    for chat in chats.data:
        if chat["nick"] == nick:
            chat_ids.append(int(chat["chat"]))
    chat_id = max(chat_ids) + 1 if chat_ids else 0
    if len(chat_ids) >= MAX_CHATS_PER_USER:
        del_chat_history(nick, min(chat_ids))
    cache = get_user_context(nick)

    max_content_len = 64
    chats.push(
        {
            "nick": nick,
            "chat": chat_id,
            "headline": cache[-1]["content"][:max_content_len],
        }
    )
    message_history.push(
        [
            {
                "nick": nick,
                "role": message["role"],
                "chat": chat_id,
                "message": message["content"],
            }
            for message in cache
        ]
    )


def pastebin(text) -> str:
    url = "https://s.h4ks.com/api/"
    with tempfile.NamedTemporaryFile(suffix=".txt") as f:
        with open(f.name, "wb") as file:
            file.write(text.encode("utf-8"))
        response = requests.post(
            url,
            files={"file": open(file.name, "rb")},
        )
    try:
        obj = response.json()
    except json.JSONDecodeError:
        response.raise_for_status()
        return response.text

    if "url" in obj:
        return obj["url"]
    if "error" in obj:
        return f"error: {obj['error']}"
    return f"error: {obj}"


async def ai_respond(
    messages: list[dict], provider: str | None = None, model: str | None = None
) -> str:
    """Generate a response from the AI."""
    headers = {"Content-Type": "application/json"}
    params = {"provider": provider, "model": model}
    data = {"messages": messages}
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/completions", headers=headers, json=data, params=params
        )
        response.raise_for_status()
        chat_completion = response.json()
        return chat_completion.get("completion", "-")
    except requests.exceptions.RequestException as e:
        raise Exception(f"API request failed: {e}")


def preprocess(text: str) -> List[str]:
    """Preprocess the text to be sent to the bot.

    Consider irc line limit
    """
    return [text[i : i + MAX_MESSAGE_LEN] for i in range(0, len(text), MAX_MESSAGE_LEN)]


def generate_formatted_ai_response(nickname: str, text: str) -> List[str]:
    """Format the text to be sent to the channel."""
    lines = format_line_breaks(markdown_to_irc(text, syntax_highlighting=True))
    lines.append("--------- END ---------")
    return lines


def format_provider(provider_name: str, provider_data: dict) -> str:
    """Format the provider."""
    name = provider_name
    url = provider_data.get("url", "")
    supported_models = ", ".join(provider_data.get("supported_models", []))
    return f"{name} url={url} models=[{supported_models}]"


def list_providers(
    _, message: message_module.Message
) -> list[message_module.Message] | str:
    """List all providers."""
    text = message.text
    m = re.match(r"^!(\S+) (.*)$", text)
    Message(channel=message.channel, is_private=False, message="Check my DM!")
    if m is None or len(m.groups()) < 2:
        return [
            Message(channel=message.nick, message=m, is_private=True)
            for m in [
                format_provider(p_name, p_data)
                for p_name, p_data in all_providers.items()
            ]
        ]
    arg = m.group(2)
    if arg.lower() in ["all", "-a", "a"]:
        return [
            Message(channel=message.nick, message=m, is_private=True)
            for m in [
                format_provider(p_name, p_data)
                for p_name, p_data in all_providers.items()
            ]
        ]
    return f"{message.nick}: Unknown argument {arg}. Valid arguments are: all, -a, a"


async def parse_command(
    match: re.Match,
    message: message_module.Message,
    model: str | None = None,
):
    context = get_user_context(message.nick)
    text = message.text
    m = re.match(r"^!(\S+) (.*)$", text)
    if m is None or len(m.groups()) != 2:
        return f"{message.nick}: What?"

    command = m.group(1).lower()

    if model is None:
        # Try to find a model based on the command
        if command in model_map:
            model = model_map[command]
        else:
            # If command is a provider name, pick the first supported model
            provider_data = all_providers.get(command.capitalize())
            if provider_data and provider_data.get("supported_models"):
                model = provider_data["supported_models"][0]
            else:
                return f"{message.nick}: Model or provider '{command}' not found. Try !list or !providers."

    text = m.group(2)
    context.append({"role": "user", "content": text})
    try:
        response = await ai_respond(list(context), model=model, provider=command)
        context.append({"role": "assistant", "content": response})
        await bot.reply(message, generate_formatted_ai_response(message.nick, response))
    except Exception as e:
        return f"{message.nick}: {e} Try another provider/model"


async def any_provider(match: re.Match, message: message_module.Message):
    """Use any provider that works, not necessarily gpt."""
    text = message.text
    context = get_user_context(message.nick)

    m = re.match(r"^!(\S+) (.*)$", text)
    if m is None or len(m.groups()) != 2:
        return f"{message.nick}: What? Usage: !gpt <text>"

    text = m.group(2)
    if not text:
        return f"{message.nick}: No text provided. Usage: !gpt <text>"
    context.append({"role": "user", "content": text})
    try:
        response = await ai_respond(list(context))
        context.append({"role": "assistant", "content": response})
        await bot.reply(message, generate_formatted_ai_response(message.nick, response))
    except Exception as e:
        return f"{message.nick}: {e} Try another provider/model"


async def get_info(match: re.Match, message: message_module.Message):
    provider_str = match.group(1)
    provider_data = all_providers.get(provider_str.capitalize())
    if provider_data is None:
        return f"{message.nick}: Provider '{provider_str}' not found. Try !list or !providers."
    return (
        f"{message.nick}: {format_provider(provider_str.capitalize(), provider_data)}"
    )


async def clear_context(match: re.Match, message: message_module.Message):
    get_user_context(message.nick).clear()
    return f"{message.nick}: Context cleared."


async def test_provider(
    provider_name: str,
    provider_data: dict,
    queue: asyncio.Queue,
    semaphore: asyncio.Semaphore,
) -> bool:
    """Sends hi to a provider and check if there is response or error."""
    async with semaphore:
        try:
            messages = [{"role": "user", "content": "hi"}]
            if not provider_data.get("supported_models"):
                result = False
            else:
                model = provider_data["supported_models"][0]
                async with asyncio.timeout(10):
                    text = await ai_respond(messages, model=model)
                result = bool(text) and isinstance(text, str)
        except Exception:
            result = False

        await queue.put((provider_name, result))
    return result


working_providers_cache = TTLCache(maxsize=1, ttl=60 * 60)
lock = asyncio.Lock()


async def on_connect():
    for channel in CHANNELS:
        await bot.join(channel)
    await bot.send_raw(f"MODE {bot.nick} +B")


if __name__ == "__main__":
    for command, help, command_help in COMMANDS:
        lower_name = command.lower()
        if command in ["list", "providers"]:
            func = list_providers
        elif command == "info":
            func = get_info
        elif command == "clear":
            func = clear_context
        elif command == "paste":

            async def _func_paste(match, message):
                text = "\n".join(
                    [
                        f'{m["role"]}: {m["content"]}'
                        for m in get_user_context(message.nick)
                    ]
                )
                return pastebin(text)

            func = _func_paste

        elif command == "gpt":
            func = any_provider

        elif command == "save":

            async def _func_save(match, message):
                save_chat_history(message.nick)
                return f"{message.nick}: Chat saved!"

            func = _func_save

        elif command == "load":

            async def _func_load(match, message):
                text = message.text
                m = re.match(r"^!(\S+) (.*)$", text)
                if m is None or len(m.groups()) < 2:
                    return f"{message.nick}: Chat id is required as an argument. Use !history to list all chats."
                arg = m.group(2)
                if not arg.isdigit():
                    return f"{message.nick}: Chat id must be an integer. Use !history to list all chats."
                try:
                    load_chat_history(message.nick, int(arg))
                except KeyError:
                    return f"{message.nick}: Chat id {arg} not found. Use !history to list all chats."
                return f"{message.nick}: Chat loaded!"

            func = _func_load

        elif command == "history":

            async def _func_list(match, message):
                chatlist = list_chats(message.nick)
                if len(chatlist) == 0:
                    return f"{message.nick}: No saved chats found."
                return chatlist

            func = _func_list

        elif command in model_map or command.capitalize() in all_providers:
            model_name_for_command = None
            if command in model_map:
                model_name_for_command = model_map[command]
            elif command.capitalize() in all_providers:
                provider_data = all_providers[command.capitalize()]
                if provider_data.get("supported_models"):
                    model_name_for_command = provider_data["supported_models"][0]

            def _wrap(model):
                async def _func(match, message):
                    if model is None:
                        return f"{message.nick}: No working model found for {command}"
                    return await parse_command(match, message, model=model)

                return _func

            func = _wrap(model_name_for_command)

        else:
            # Check if it's a dynamic provider or model command
            if command in model_map or command.capitalize() in all_providers:
                model_name_for_command = None
                if command in model_map:
                    model_name_for_command = model_map[command]
                elif command.capitalize() in all_providers:
                    provider_data = all_providers[command.capitalize()]
                    if provider_data.get("supported_models"):
                        model_name_for_command = provider_data["supported_models"][0]

                def _wrap(model):
                    async def _func(match, message):
                        if model is None:
                            return (
                                f"{message.nick}: No working model found for {command}"
                            )
                        return await parse_command(match, message, model=model)

                    return _func

                func = _wrap(model_name_for_command)
            else:
                func = parse_command

        bot.arg_commands_with_message[lower_name] = {
            "function": func,
            "acccept_pms": True,
            "pass_data": False,
            "help": help,
            "command_help": command_help,
            "simplify": None,
        }

    utils.set_loglevel(logging.DEBUG)
    bot.run_with_callback(on_connect)
