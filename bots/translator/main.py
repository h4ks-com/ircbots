import concurrent.futures
import json
import logging
import os
import re
from collections import deque
from copy import deepcopy
from functools import lru_cache
from hashlib import md5

from IrcBot.bot import Color, IrcBot, Message, ReplyIntent, utils
from IrcBot.utils import debug, log

HOST = os.getenv("IRC_HOST")
assert HOST, "IRC_HOST is required"
SSL = os.getenv("IRC_SSL") == "true"
PORT = int(os.getenv("IRC_PORT") or 6667)
NICK = os.getenv("NICK") or "_bqbot"
PASSWORD = os.getenv("PASSWORD") or ""
CHANNELS = json.loads(os.getenv("CHANNELS") or "[]")

ACCEPT_PRIVATE_MESSAGES = True
DBFILEPATH = NICK + ".db"
PROMPT = ">>> "
CACHE_SIZE = 1024
WAIT_TIMEOUT = 60
PROXIES = []

# A maximum of simultaneously auto translations for each users
MAX_AUTO_LANGS = 2
MAX_BABEL_MSG_COUNTER = 20
MAX_BACK_TRANSLATIONS = 10

INFO_CMDS = {
    r"^@linux.*$": "The OS this bot runs on",
    "^@rules.*$": [
        f"1. You can only have {MAX_AUTO_LANGS} auto translations active simultaneously",
        "2. Do not use this bot to spam",
    ],
    "^@help\s+auto.*$": [
        "@auto [src_iso_code] [dest_iso_code] Sets the automatic translation from the source language to the target language as specified in the command.",
        "@auto show Displays the current source and target language or languages in place in the channel.",
        "@auto off [dest_iso_code] Disables automatic translation to the specified target language.",
        "@auto off Clears all rules for automatic translations in the channel.",
    ],
    "^@help\s+babel.*$": [
        "@babel [dest_iso_code] You will receve translations of this chat in the specified language as a PM from me.",
        "@babel off  Disables babel mode.",
        "Notice that this mode won't last forever, you have to be active on the channel to keep babel mode active.",
    ],
    "^@help\s+back.*$": [
        "@back [nick] [dest_iso_code] [N] Translates the Nth last message from the specified nick to the specified language. If N is not specified will translate the last",
    ],
    "^@help.*": [
        "@: Manually sets the target language for the current line, like so: @es Hello friends. This should translate 'Hello friends' to Spanish. The source language is detected automatically.",
        "Language iso codes: http://ix.io/2HAN, or https://cloud.google.com/translate/docs/languages",
        "@auto: automatically translate everything you send. Use '@help auto' for more info.",
        "@babel: automatically translate a chat and sends every message to you as a PM. Use '@help babel' for more info.",
        "@back: Translates a recent user message. Usate '@help back' for more info.",
        "@reset: resets your babel preferences",
    ],
    #    r"^(.*) linux ": "Do you mean the best OS?",
    #    r"^(.*) vim ": "Do you mean the best Text editor???",
}

LANGS = [l.strip() for l in open("google_iso_lang_codes.txt").readlines()]

utils.setParseOrderTopBottom()
##################################################
# BOT COMMANDS DEFINITIONS                       #
##################################################

from googletrans import Translator as google_translator

auto_nicks = {}

for r in INFO_CMDS:

    @utils.regex_cmd(r, ACCEPT_PRIVATE_MESSAGES)
    def info_cmd(m, regexp=r):
        return INFO_CMDS[regexp]


@lru_cache(maxsize=CACHE_SIZE)
def trans(m, dst, src="auto", autodetect=True):
    if type(m) != str:
        m = m.group(1)

    # Removing nicknames
    match = re.match(r"^(\w+?\s*:\s*)?(.+)", m)
    head = match[1] if match[1] else ""
    m = match[2]

    logging.info("Translating: " + m)
    proxy_index = None
    while proxy_index is None or proxy_index < len(PROXIES):
        translator = google_translator(proxies={"http": PROXIES[proxy_index]} if proxy_index is not None else None)
        try:
            detected_lang = translator.detect(m).lang
            if autodetect and detected_lang == dst:
                logging.info("1. Ignoring source equals destination: " + m)
                logging.info(f"Source: {detected_lang}  Destination: {dst}")
                return
            if autodetect and src != "auto" and not detected_lang.startswith(src):
                logging.info("2. Ignoring source equals destination: " + m)
                logging.info(f"Source: {detected_lang}  Destination: {dst}")
                return
            msg = translator.translate(m, dest=dst, src=src)
            return head + str(msg.text)
        except Exception as e:
            return str(e)


def translate(m, message, dst, src="auto", autodetect=True):
    translated_msg = trans(m, dst, src, autodetect)
    if translated_msg:
        return Message(
            message=f"  <{message.sender_nick} ({dst.upper()})> {translated_msg}",
            channel=message.channel,
        )


@utils.regex_cmd_with_messsage(r"^@(\S\S)(?::(\S\S))?\s(.*)$", ACCEPT_PRIVATE_MESSAGES)
def translate_cmd(m, message):
    src = m.group(1)
    dst = m.group(2)
    text = m.group(3)
    print(f"{src=}")
    print(f"{dst=}")
    print(f"{text=}")
    lang = src if dst is None else dst
    if lang not in LANGS:
        return f"<{message.nick}> {lang} is not a valid language code!"
    if dst and dst not in LANGS:
        return f"<{message.nick}> {dst} is not a valid language code!"
    return translate(text, message, lang, src=src if dst else "auto", autodetect=not dst)


@utils.regex_cmd_with_messsage("^@auto (.*)$", ACCEPT_PRIVATE_MESSAGES)
def auto_conf(m, message):
    src = m.group(1).strip()
    if src == "show":
        if message.nick in auto_nicks and message.channel in auto_nicks[message.nick]:
            langs = []
            for au in auto_nicks[message.nick][message.channel]:
                langs.append(f"{au['src']}->{au['dst']}")
            return f"<{message.nick}> {'; '.join(langs)}"
        else:
            return f"<{message.nick}> You don't have any auto rule set on this channel."

    if src == "off":
        if message.nick in auto_nicks and message.channel in auto_nicks[message.nick]:
            for au in deepcopy(auto_nicks[message.nick][message.channel]):
                auto_nicks[message.nick][message.channel].remove(au)
            return f"<{message.nick}> all of yours auto translate rules were cleaned for this channel!"
        else:
            return f"<{message.nick}> You don't have any auto rule set on this channel."


@utils.regex_cmd_with_messsage("^@auto (.*) (.*)$", ACCEPT_PRIVATE_MESSAGES)
def auto(m, message):
    src = m.group(1).strip()
    dst = m.group(2).strip()

    if src == "off":
        if message.nick in auto_nicks and message.channel in auto_nicks[message.nick]:
            for au in deepcopy(auto_nicks[message.nick][message.channel]):
                ct = 0
                if au["dst"] == dst:
                    auto_nicks[message.nick][message.channel].remove(au)
                    ct += 1
            if ct:
                return f"<{message.nick}> Cleaned auto translations for {dst}"
            else:
                return f"<{message.nick}> {dst} is not set for you"
        else:
            return f"<{message.nick}> You don't have any auto rule set on this channel."

    if len(dst) != 2 or len(src) != 2:
        return f"<{message.nick}> Please enter two ISO codes separated by spaces that have two letters. Like `@auto en es`. See here -> https://cloud.google.com/translate/docs/languages"

    if message.nick not in auto_nicks:
        auto_nicks[message.nick] = {}
    if message.channel not in auto_nicks[message.nick]:
        auto_nicks[message.nick][message.channel] = []
    if len(auto_nicks[message.nick][message.channel]) >= MAX_AUTO_LANGS:
        return f"<{message.nick}> You have you reached the maximum of {MAX_AUTO_LANGS} allowed simultaneously auto translations"

    if src not in LANGS:
        return f"<{message.nick}> {src} is not a valid language code!"
    if dst not in LANGS:
        return f"<{message.nick}> {dst} is not a valid language code!"
    au = {
        "channel": message.channel,
        "nick": message.sender_nick,
        "src": src,
        "dst": dst,
    }
    if au in auto_nicks[message.nick][message.channel]:
        return f"<{message.nick}> Skipping existing rule!"
    auto_nicks[message.nick][message.channel].append(au)
    return f"<{message.nick}> rule added!"


back_messages = {}

# Implement back translations
@utils.regex_cmd_with_messsage("^@back (.*)$", ACCEPT_PRIVATE_MESSAGES)
def back(m, message):
    global back_messages
    args = m.group(1).strip().split()
    if len(args) < 2:
        return f"<{message.nick}> Usage: @back <lang> <message> [n]"
    nick = args[0]
    dst = args[1]
    if dst not in LANGS:
        return f"<{message.nick}> {dst} is not a valid language code!"
    n = 1
    if len(args) > 2:
        if args[2].isdigit():
            n = int(args[2])
        else:
            return f"<{message.nick}> The third argument must be a number"
        if n > MAX_BACK_TRANSLATIONS:
            return f"<{message.nick}> You should use a number less than {MAX_BACK_TRANSLATIONS}"

    if message.channel not in back_messages:
        return f"<{message.nick}> No messages found for this channel"
    if nick not in back_messages[message.channel]:
        return f"<{message.nick}> No messages found for {nick} on this channel"
    cached = back_messages[message.channel][nick]
    if len(cached) < n:
        return f"<{message.nick}> There are only {len(cached)} messages for {nick} on this channel"
    text = cached[-n]
    translated_msg = trans(text, dst, "auto") or text
    return Message(
        message=f"  <{message.sender_nick} ({dst.upper()})> {translated_msg}",
        channel=message.channel,
    )


babel_users = {}
babel_prefs = {}


@utils.regex_cmd_with_messsage("^@babel (.*)$", ACCEPT_PRIVATE_MESSAGES)
def babel(m, message):
    global babel_users, babel_prefs
    dst = m.group(1).strip()
    nick = message.sender_nick
    channel = message.channel
    if channel not in babel_users:
        babel_users[channel] = {}
    if dst == "off":
        if nick in babel_users[channel]:
            del babel_users[channel][nick]
            del babel_prefs[nick]
            return f"<{message.nick}> Babel mode disabled"
        else:
            return f"<{message.nick}> You do not have babel mode enabled"
    if dst not in LANGS:
        return f"<{message.nick}> {dst} is not a valid language code!"
    babel_users[channel][nick] = {"channel": channel, "dst": dst, "counter": 0}
    babel_prefs[nick] = {}
    return Message(
        message=f"<{message.nick}> Babel mode enabled. You will now receive translations in {dst} as private messages for this channel: {channel}",
        channel=message.nick,
        is_private=True,
    )


def babel_warning(m, message, babel_nick, dst, src="en"):
    translated_msg = trans(m, dst, src)
    if translated_msg:
        return Message(
            message=f"<{babel_nick}> {translated_msg}",
            channel=babel_nick,
            is_private=True,
        )


COLORS = [
    Color.red,
    Color.navy,
    Color.light_gray,
    Color.maroon,
    Color.blue,
    Color.magenta,
    Color.purple,
    Color.green,
    Color.yellow,
    Color.orange,
    Color.cyan,
    Color.light_green,
]


def colorize(text):
    # use hash and colors to colorize text
    _hash = int(md5(text.strip().encode()).hexdigest(), 16)
    return Color(text, COLORS[_hash % len(COLORS)]).str + Color.esc


def babel_message(m, message, babel_nick, dst, src="auto"):
    if type(m) != str:
        m = m.group(1)
    translated_msg = trans(m, dst, src)
    if not translated_msg:
        translated_msg = m
    return Message(
        message=f"  \x02({colorize(message.channel)}) <{colorize(message.nick)}>\x02 {translated_msg}",
        channel=babel_nick,
        is_private=True,
    )


async def ask(
    bot: IrcBot,
    nick: str,
    question: str,
    expected_input=None,
    repeat_question=None,
    loop: bool = True,
    timeout_message: str = "Response timeout!",
):
    await bot.send_message(question, nick)
    resp = await bot.wait_for("privmsg", nick, timeout=WAIT_TIMEOUT)
    while loop:
        if resp:
            if expected_input is None or resp.get("text").strip() in expected_input:
                break
            await bot.send_message(repeat_question if repeat_question else question, nick)
        else:
            await bot.send_message(timeout_message, nick)
            break
        resp = await bot.wait_for("privmsg", nick, timeout=WAIT_TIMEOUT)
    return resp.get("text").strip() if resp else None


@utils.regex_cmd_with_messsage("^@reset$", ACCEPT_PRIVATE_MESSAGES)
def reset_babel(m, message):
    global babel_prefs
    babel_prefs[message.nick] = {}
    return Message(
        message=f"<{message.nick}> Reset babel preferences",
        channel=message.nick,
        is_private=True,
    )


@utils.regex_cmd_with_messsage("^(.*)$", ACCEPT_PRIVATE_MESSAGES)
async def process_auto(bot: IrcBot, m, message):
    global babel_users, babel_prefs, back_messages
    channel = message.channel
    if channel not in back_messages:
        back_messages[channel] = {}
    if message.nick not in back_messages[channel]:
        back_messages[channel][message.nick] = deque(maxlen=MAX_BACK_TRANSLATIONS)
    back_messages[channel][message.nick].append(message.text)

    if channel not in babel_users:
        babel_users[channel] = {}

    # reset babel counter on activity
    if (message.channel in babel_users or message.channel == message.nick) and message.nick in babel_users[
        message.channel
    ]:
        babel_users[message.channel][message.nick]["counter"] = 0
        logging.info(f"Reset babel counter for {message.nick} in {message.channel}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_translations = {}
        # Auto mode
        if message.nick in auto_nicks and message.channel in auto_nicks[message.nick]:
            future_translations.update(
                {
                    executor.submit(translate, m, message, au["dst"], au["src"]): m[1]
                    for au in auto_nicks[message.nick][message.channel]
                }
            )

        # Send translations for babel users of this channel
        BABEL_WARN_THRESHOLD = 5
        for babel_nick in deepcopy(babel_users[message.channel]):
            babel_users[message.channel][babel_nick]["counter"] += 1
            dst = babel_users[message.channel][babel_nick]["dst"]
            if babel_users[channel][babel_nick]["counter"] >= MAX_BABEL_MSG_COUNTER:
                future_translations.update(
                    {
                        executor.submit(
                            babel_warning,
                            f"You've been inactive for too long! You will no longer receive translations for {message.channel}",
                            message,
                            babel_nick,
                            dst,
                            "en",
                        ): f"babel_over: {m[1]}"
                    }
                )
                del babel_users[channel][babel_nick]
                del babel_prefs[babel_nick]
                continue
            elif babel_users[channel][babel_nick]["counter"] == MAX_BABEL_MSG_COUNTER - BABEL_WARN_THRESHOLD:
                future_translations.update(
                    {
                        executor.submit(
                            babel_warning,
                            f"You will stop receiving translations for {message.channel} in {BABEL_WARN_THRESHOLD} messages. Say something here or on that channel.",
                            message,
                            babel_nick,
                            dst,
                            "en",
                        ): f"babel_warning: {m[1]}"
                    }
                )

            future_translations.update(
                {
                    executor.submit(
                        babel_message,
                        m,
                        message,
                        babel_nick,
                        dst,
                    ): f"babel_warning: {m[1]}"
                }
            )

        for future in concurrent.futures.as_completed(future_translations):
            text = future_translations[future]
            try:
                data = future.result()
            except Exception as exc:
                logging.info("%r generated an exception: %s" % (text, exc))
            else:
                await bot.send_message(data)

    # Babel mode
    if message.channel == message.nick:
        nick = message.nick
        if nick not in babel_prefs:
            return

        logging.debug(f"Babel mode for {nick} triggered")
        if "channel" not in babel_prefs[nick]:
            babel_channels = []
            for babel_channel in babel_users:
                for babel_nick in babel_users[babel_channel]:
                    if babel_nick == nick:
                        babel_channels.append(babel_channel)
                        dst = babel_users[babel_channel][babel_nick]["dst"]
            logging.debug(f"Babel channels for {nick}: {babel_channels}")

            if len(babel_channels) > 1:
                resp = await ask(
                    bot,
                    message.nick,
                    PROMPT
                    + trans(
                        "To what chat do you want to reply to?",
                        src="en",
                        dst=dst,
                        autodetect=False,
                    )
                    + ". One of: "
                    + ", ".join(babel_channels),
                    expected_input=babel_channels,
                    timeout_message=PROMPT
                    + trans(
                        "Sorry but you took too long to reply!",
                        src="en",
                        dst=dst,
                        autodetect=False,
                    ),
                    repeat_question=PROMPT
                    + trans(
                        "Sorry, please choose one of these channels to send to:",
                        src="en",
                        dst=dst,
                        autodetect=False,
                    )
                    + ", ".join(babel_channels),
                )
                if not resp:
                    return
                babel_prefs[nick]["channel"] = resp
            else:
                babel_prefs[nick]["channel"] = babel_channels[0]

        if "dst" not in babel_prefs[nick]:
            logging.debug(f"{babel_prefs[nick]['channel']=}")
            dst = babel_users[babel_prefs[nick]["channel"]][nick]["dst"]
            resp = await ask(
                bot,
                message.nick,
                PROMPT
                + trans(
                    "To what language you want to translate to? Send a 2 letter iso code.",
                    src="en",
                    dst=dst,
                    autodetect=False,
                ),
                expected_input=LANGS,
                timeout_message=PROMPT
                + trans(
                    "Sorry but you took too long to reply!",
                    src="en",
                    dst=dst,
                    autodetect=False,
                ),
                repeat_question=PROMPT
                + trans(
                    "That is an invalid iso code! These are valid:",
                    src="en",
                    dst=dst,
                    autodetect=False,
                )
                + "http://ix.io/2HAN",
            )
            if not resp:
                return
            babel_prefs[nick]["dst"] = resp
            await bot.send_message(
                trans(
                    f"Sending to channel \"{babel_prefs[nick]['channel']}\" translating to language \"{babel_prefs[nick]['dst']}\". You can always reset this with \"@reset\"",
                    src="en",
                    dst=dst,
                    autodetect=False,
                ),
                nick,
            )

        msg = trans(m, dst=babel_prefs[nick]["dst"])
        msg = msg if msg else m[1]
        await bot.send_message(
            f" \x02<{nick}>\x02 {msg}",
            babel_prefs[nick]["channel"],
        )

        babel_users[babel_prefs[nick]["channel"]][message.nick]["counter"] = 0
        logging.info(f"Reset babel counter for {message.nick} in {babel_prefs[nick]['channel']}")


if __name__ == "__main__":
    bot = IrcBot(HOST, PORT, NICK, CHANNELS, PASSWORD, use_ssl=SSL)
    bot.run()
