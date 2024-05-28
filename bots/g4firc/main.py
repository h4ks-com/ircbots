import asyncio
import json
import os
import re
from collections import deque
from functools import lru_cache
from typing import List

import g4f
import requests
from cachetools import TTLCache
from dotenv import load_dotenv
from g4f import Provider, ProviderType
from g4f.client import AsyncClient
from g4f.stubs import ChatCompletion
from ircbot import Color, IrcBot, Message
from ircbot.client import MAX_MESSAGE_LEN, PersistentData
from ircbot.format import format_line_breaks, markdown_to_irc

load_dotenv()

NICK = os["NICK"]
SERVER = os["SERVER"]
CHANNELS = json.loads(os["CHANNELS"])
PORT = int(os.get("PORT") or 6667)
PASSWORD = os["PASSWORD"] if "PASSWORD" in os else None
SSL = os["SSL"] == "true"
DATABASE = os.get("DATABASE") or "database.db"
MAX_CHATS_PER_USER = int(os.get("MAX_CHATS_PER_USER") or 10)

PROVIDER_BLACKLIST = ["bing"]

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
        "providers",
        "List all providers",
        "Lists available providers with their info like model and url. Same as list",
    ),
    ("gpt3", "Generate text with GPT-3", "Generates text with GPT-3.5"),
    ("gpt4", "Generate text with GPT-4", "Generates text with GPT-4"),
    ("gpt", "Generate text with GPT-4", "Generates text with GPT-4. Same as gpt4"),
    ("llama", "Generate text with Llama", "Generates text with Llama"),
    ("falcon", "Generate text with Falcon", "Generates text with Falcon"),
    ("davinci", "Generate text with Davinci", "Generates text with Davinci"),
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
    (
        "selftest",
        "Self tests the bot",
        "Tests all providers",
    ),
]

model_map = {
    "gpt3": "gpt-3.5-turbo",
    "gpt4": "gpt-4",
    "gpt": "gpt-4",
    "llama": "llama-13b",
    "falcon": "falcon-40b",
    "davinci": "text-davinci-003",
}


def get_provider_name(provider):
    return provider.__name__


# Load all modules from Provider.Providers
providers = list(Provider.ProviderUtils.convert.values())
providers = [
    provider
    for provider in providers
    if not provider.needs_auth
    and get_provider_name(provider).lower() not in PROVIDER_BLACKLIST
]

command_to_provider = {
    get_provider_name(provider).lower(): provider for provider in providers
}

all_models = [
    getattr(g4f.models, model_name)
    for model_name in dir(g4f.models)
    if isinstance(getattr(g4f.models, model_name), g4f.models.Model)
]
for provider in providers:
    name = get_provider_name(provider)
    model = []
    if provider.supports_gpt_35_turbo:
        model.append("gpt-3.5-turbo")
    if provider.supports_gpt_4:
        model.append("gpt-4")

    for m in all_models:
        if m.best_provider == provider:
            model.append(m.name)

    provider.model = model
    if not hasattr(provider, "url"):
        continue
    url = provider.url
    COMMANDS.append(
        (
            name.lower(),
            f"Generate text with {name}",
            f"Generates text with {name}. {model=} {url=}",
        )
    )


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
    url = "http://ix.io"
    payload = {"f:1=<-": text}
    response = requests.request("POST", url, data=payload)
    return response.text


async def ai_respond(messages: list[dict], model: str, provider: ProviderType) -> str:
    """Generate a response from the AI."""
    client = AsyncClient()
    chat_completion: ChatCompletion = await client.chat.completions.create(
        messages=messages, model=model, provider=provider, stream=False
    )
    choices = chat_completion.choices
    if len(choices) == 0:
        raise Exception("No response from the provider")
    return choices[0].message.content


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


def format_provider(provider: Provider.BaseProvider) -> str:
    """Format the provider."""
    name = get_provider_name(provider)
    model = str(provider.model)[:64]
    if not hasattr(provider, "url"):
        url = ""
    else:
        url = provider.url
    working = (
        Color("Yes", fg=Color.green).str
        if provider.working
        else Color("No", fg=Color.red).str
    )
    return f"{name} {model=} {url=} -- available: {working}"


def list_providers(_, message: Message) -> list[Message] | str:
    """List all providers."""
    text = message.text
    m = re.match(r"^!(\S+) (.*)$", text)
    Message(channel=message.channel, is_private=False, message="Check my DM!")
    if m is None or len(m.groups()) < 2:
        return [
            Message(channel=message.nick, message=m, is_private=True)
            for m in [format_provider(p) for p in providers if p.working]
        ]
    arg = m.group(2)
    if arg.lower() in ["all", "-a", "a"]:
        return [
            Message(channel=message.nick, message=m, is_private=True)
            for m in [format_provider(p) for p in providers]
        ]
    return f"{message.nick}: Unknown argument {arg}. Valid arguments are: all, -a, a"


async def parse_command(
    match: re.Match,
    message: Message,
    model: str | None = None,
    provider=None,
):
    context = get_user_context(message.nick)
    text = message.text
    m = re.match(r"^!(\S+) (.*)$", text)
    if m is None or len(m.groups()) != 2:
        return f"{message.nick}: What?"

    if provider is None:
        command = m.group(1)
        provider = command_to_provider.get(command)
        if provider is None:
            provider = command_to_provider.get(command.lower())

        if provider is None:
            return f"{message.nick}: Provider '{command}' not found. Try !list or !providers."

    if model is None:
        model = provider.model[0]

    text = m.group(2)
    context.append({"role": "user", "content": text})
    try:
        response = await ai_respond(list(context), model, provider=provider)
        context.append({"role": "assistant", "content": response})
        await bot.reply(message, generate_formatted_ai_response(message.nick, response))
    except Exception as e:
        return f"{message.nick}: {e} Try another provider"


async def get_info(match: re.Match, message: Message):
    provider_str = match.group(1)
    provider = command_to_provider.get(provider_str)
    if provider is None:
        return f"{message.nick}: Provider '{provider_str}' not found. Try !list or !providers."
    return f"{message.nick}: {format_provider(provider)}"


async def clear_context(match: re.Match, message: Message):
    get_user_context(message.nick).clear()
    return f"{message.nick}: Context cleared."


async def test_provider(
    provider: ProviderType, queue: asyncio.Queue, semaphore: asyncio.Semaphore
) -> bool:
    """Sends hi to a provider and check if there is response or error."""
    async with semaphore:
        try:
            messages = [{"role": "user", "content": "hi"}]
            model = provider.model[0]
            async with asyncio.timeout(10):
                text = await ai_respond(messages, model, provider=provider)
            result = bool(text) and isinstance(text, str)
        except Exception:
            result = False

        await queue.put((provider, result))
    return result


working_providers_cache = TTLCache(maxsize=1, ttl=60 * 60)
lock = asyncio.Lock()


async def selftest(match: re.Match, message: Message):
    """Test all providers."""
    if lock.locked():
        return f"{message.nick}: Self test is already running. Please wait."

    if "working_providers" in working_providers_cache:
        working_providers = working_providers_cache["working_providers"]
        return f"{message.nick}: Working providers: " + ", ".join(working_providers)

    async with lock:
        await bot.send_message(
            Message(
                channel=message.channel,
                message=f"{message.nick} Checking working providers....",
                is_private=False,
            )
        )

        results = {}

        queue = asyncio.Queue()

        async def producer():
            semaphore = asyncio.Semaphore(8)
            await asyncio.gather(
                *[test_provider(provider, queue, semaphore) for provider in providers]
            )
            await queue.join()
            await queue.put((None, None))

        async def consumer():
            async with asyncio.timeout(5 * 60):
                while True:
                    provider, result = await queue.get()
                    if provider is None and result is None:
                        break
                    name = get_provider_name(provider)
                    results[name] = result
                    queue.task_done()

        await asyncio.gather(producer(), consumer())

        working_providers = [p for p, r in results.items() if r]
        working_providers_cache["working_providers"] = working_providers
        return f"{message.nick}: Working providers: " + ", ".join(working_providers)


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

            async def _func_paste(bot, match, message):
                text = "\n".join(
                    [
                        f'{m["role"]}: {m["content"]}'
                        for m in get_user_context(message.nick)
                    ]
                )
                return pastebin(text)

            func = _func_paste

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

        elif command in model_map:
            model = model_map[command]
            for provider in providers:
                if model in provider.model and provider.working:
                    break
            else:
                provider = None

            def _wrap(provider, model):
                async def _func(bot, match, message):
                    if provider is None:
                        return f"{message.nick}: No working provider found for {model=}"
                    return await parse_command(
                        bot, match, message, model=model, provider=provider
                    )

                return _func

            func = _wrap(provider, model)

        elif command == "selftest":
            func = selftest
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

    bot.run_with_callback(on_connect)
