import json
import logging
import os
import shlex
import subprocess
import sys
import time

from cleverbot import Cleverbot
from dotenv import load_dotenv
from ircbot import IrcBot, utils
from ircbot.message import Message

load_dotenv()

HOST = os.getenv("IRC_HOST")
assert HOST, "IRC_HOST is required"
SSL = os.getenv("IRC_SSL") == "true"
PORT = int(os.getenv("IRC_PORT") or 6667)
NICK = os.getenv("NICK") or "_bqbot"
PASSWORD = os.getenv("PASSWORD") or ""
CHANNELS = json.loads(os.getenv("CHANNELS") or "[]")

##################################################
# SETTINGS                                       #
##################################################

LOGFILE = None
LEVEL = logging.INFO
ADMINS = ["mattf", "handyc", "loudercake"]
PREFIX = "-"
MASTER = True
GONZOBOT = "_cloudbot"


if len(sys.argv) >= 2:
    NICK = sys.argv[1]
    MASTER = False

if len(sys.argv) >= 3:
    CHANNELS = [sys.argv[2]]

##################################################

sessions: dict[str, Cleverbot] = {}
BotType = Cleverbot

# Initialize bot
bot = IrcBot(
    HOST, PORT, NICK, CHANNELS, PASSWORD, use_ssl=SSL, capabilities=["message-tags"]
)
utils.set_loglevel(LEVEL)
bot.set_prefix(PREFIX)


def reply(session: str, text: str) -> str:
    global sessions, BotType
    if session not in sessions:
        sessions[session] = BotType()
    return sessions[session].send(text)


@bot.regex_cmd_with_message(
    rf".*<{NICK}> (\S+) has challenged you to a duel!.*$", False
)
def duelaccept(args, message: Message) -> str | None:
    nick = message.sender_nick
    if nick == GONZOBOT:
        return f".accept {args.group(1)}"
    return None


@bot.regex_cmd_with_message(
    rf"<(\S+)> {NICK} has accepted your duel request! The duel will begin in (\d+) seconds.",
    False,
)
def duelbang(args, message: Message) -> str | None:
    nick = message.sender_nick
    if nick == GONZOBOT:
        delay = 0.05
        time.sleep(int(args.group(2)) - delay)
        return f".bang {args.group(1)}"
    return None


@bot.regex_cmd_with_message(
    rf"(?i)^((?:.*\s)?{NICK}([\s|,|\.|\;|\?|!|:]*)(?:\s.*)?)$", False
)
def mention(args, message: Message) -> str | None:
    if message.text.strip().startswith("."):
        return None
    nick = message.sender_nick
    if nick == GONZOBOT:
        return None
    text = args.group(1).strip()
    last = args.group(2) if args.group(2) else ""
    text.replace(f" {NICK}{last}", " ")
    session = f"{NICK}_{nick}"
    return f"{nick}: {reply(session, text)}"


@bot.arg_command("restart", "Restart the bot", "")
def restart(args, message: Message) -> None:
    if message.sender_nick in ADMINS and MASTER:
        subprocess.Popen("pm2 restart ken", shell=True)


pids: dict[str, subprocess.Popen] = {}


@bot.arg_command("del", "Delete bot instances", "")
def _del(args, message: Message) -> list[str]:
    nicks = utils.m2list(args)
    output = []
    if message.sender_nick in ADMINS:
        if NICK in nicks:
            sys.exit(0)
    if message.sender_nick in ADMINS and MASTER:
        for nick in nicks:
            if nick not in pids:
                output.append(f"{message.sender_nick}: {nick} is not in use!")
                continue
            pids[nick].kill()
            pids.pop(nick)
    return output


@bot.arg_command("add", "Add bot instances", "")
def add(args, message: Message) -> str | None:
    nicks = utils.m2list(args)
    if message.sender_nick in ADMINS and MASTER:
        for nick in nicks:
            if nick in pids:
                return f"{message.sender_nick}: {nick} is already in use!"
        for nick in nicks:
            this_file = os.path.realpath(__file__)
            pids[nick] = subprocess.Popen(
                f"python3 {this_file} {shlex.quote(nick)} '{message.channel}'",
                stdout=subprocess.PIPE,
                shell=True,
            )
    return None


@bot.regex_cmd_with_message(r"^\s*YES\s*$", False)
def yes_response(args, message: Message) -> str | None:
    if NICK == "ken":
        return "NO"
    return None


##################################################
# RUNNING THE BOT                                #
##################################################


async def on_connect() -> None:
    for channel in CHANNELS:
        await bot.join(channel)
    await bot.send_raw(f"MODE {bot.nick} +B")
    await bot.send_message("Hello everyone !!!")


async def check_no_bot(bot: IrcBot, message: Message) -> bool:
    return not (message.tags and message.tags.bot)


if __name__ == "__main__":
    bot.add_middleware(check_no_bot)
    bot.run_with_callback(on_connect)
