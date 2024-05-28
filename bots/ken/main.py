import json
import logging
import os
import shlex
import subprocess
import sys
import time

from cleverbot import Cleverbot
from IrcBot.bot import IrcBot, utils
from IrcBot.utils import debug, log

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
# LEVEL = logging.INFO
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

sessions = {}
BotType = Cleverbot


def reply(session: str, text: str) -> str:
    global sessions, BotType
    if session not in sessions:
        sessions[session] = BotType(use_tor_fallback=True)
    return sessions[session].send(text)


@utils.regex_cmd_with_messsage(rf".*<{NICK}> (\S+) has challenged you to a duel!.*$", False)
def duelaccept(args, message):
    nick = message.sender_nick
    if nick == GONZOBOT:
        return f".accept {args[1]}"


@utils.regex_cmd_with_messsage(
    rf"<(\S+)> {NICK} has accepted your duel request! The duel will begin in (\d+) seconds.", False
)
def duelbang(args, message):
    nick = message.sender_nick
    if nick == GONZOBOT:
        delay = 0.05
        time.sleep(int(args[2]) - delay)
        return f".bang {args[1]}"


@utils.regex_cmd_with_messsage(rf"(?i)^((?:.*\s)?{NICK}([\s|,|\.|\;|\?|!|:]*)(?:\s.*)?)$", False)
def mention(args, message):
    if message.text.strip().startswith("."):
        return
    nick = message.sender_nick
    if nick == GONZOBOT:
        return
    text = args[1].strip()
    last = args[2] if args[2] else ""
    text.replace(f" {NICK}{last}", " ")
    session = f"{NICK}_{nick}"
    return f"{nick}: {reply(session, text)}"


@utils.arg_command("restart")
def restart(args, message):
    if message.sender_nick in ADMINS and MASTER:
        subprocess.Popen("pm2 restart ken", shell=True)


pids = {}


@utils.arg_command("del")
def _del(args, message):
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


@utils.arg_command("add")
def add(args, message):
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


@utils.regex_cmd_with_messsage(r"^\s*YES\s*$", False)
def yes_reponse(args, message):
    if NICK == "ken":
        return "NO"


##################################################
# RUNNING THE BOT                                #
##################################################


async def onConnect(bot: IrcBot):
    for channel in CHANNELS:
        await bot.join(channel)
    await bot.send_message("Hello everyone !!!")


if __name__ == "__main__":
    utils.setLogging(LEVEL, LOGFILE)
    utils.setPrefix(PREFIX)
    bot = IrcBot(HOST, PORT, NICK, PASSWORD, use_ssl=SSL)
    bot.runWithCallback(onConnect)
