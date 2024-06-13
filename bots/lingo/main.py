import asyncio
import json
import os
import random
import sqlite3
from typing import Dict

import gpt
import iso639
from dotenv import load_dotenv
from ircbot import IrcBot, Message, ReplyIntent
from ircbot.format import format_line_breaks, markdown_to_irc
from iso639 import LanguageNotFoundError

load_dotenv()

LOGFILE = None
HOST = os.getenv("IRC_HOST")
assert HOST, "IRC_HOST environment variable is required"

PORT = int(os.getenv("IRC_PORT") or 6697)
SSL = os.getenv("IRC_SSL") == "true"
NICK = os.getenv("NICK") or "lingo"
PASSWORD = os.getenv("PASSWORD")
CHANNELS = json.loads(os.getenv("CHANNELS") or "[]")


con = sqlite3.connect("user.db")
cur = con.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS user(name, language, spam)")


standalone_GPT = gpt.GPT(contextualize=False)
instances: Dict[
    str, gpt.GPT
] = {}  # is habited by dicts where nicks correspond to a GPT object
spam: Dict[
    str, int
] = {}  # is habited by dicts of the chance of triggering a bot message same way as contexts

bot = IrcBot(
    HOST,
    PORT,
    NICK,
    CHANNELS,
    PASSWORD,
    use_ssl=SSL,
)

prompts = {
    "correction": 'Briefly give me the correction, or just say "Perfect!" if it looks fine, the spelling and grammar of the following sentence in {language}: {message}',
    "answer": "Briefly respond in {language}: {message}",
    "continue": "Come up with a question or a topic to help me learn {language}. Don't use english. Say anything you want.",
}


def user_exists(user: str) -> bool:
    if cur.execute("SELECT * FROM user WHERE name=(?)", (user,)).fetchone():
        return True
    else:
        return False


def get_user_language(user: str) -> str:
    res = cur.execute("SELECT language from user WHERE name=(?)", (user,))
    isocode = res.fetchone()[0]
    return iso639.Language.from_part1(isocode).name


def get_user_spam(user: str) -> int:
    res = cur.execute("SELECT spam from user WHERE name=(?)", (user,))
    return res.fetchone()[0]


def register_spam_level(msg: Message):
    if msg.text.isdigit():
        if int(msg.text) in range(-1, 101):
            spam[msg.sender_nick] = int(msg.text)
            cur.execute(
                "UPDATE user SET spam = ? WHERE name=?",
                (int(msg.text), msg.sender_nick),
            )
            con.commit()
            return "Registration complete."
        else:
            return ReplyIntent(
                "Input given was not between 1 and 100. Please try again.",
                register_spam_level,
            )
    else:
        return ReplyIntent(
            "Inpu must be a number between 1 and 100. Please try again.",
            register_spam_level,
        )


def register_language(msg):
    try:
        iso639.Language.from_part1(msg.text)
    except LanguageNotFoundError:
        return ReplyIntent(
            'Invalid format. Please use ISO 369-1 format. Example: "de"',
            register_language,
        )
    user = (msg.sender_nick, msg.text)
    cur.execute("INSERT INTO user (name, language, spam) VALUES (?,?,100)", user)
    con.commit()
    return ReplyIntent(
        "Please add initial spam probability amount for the bot (number between 0 and 100).",
        register_spam_level,
    )


def confirm_dialog(msg):
    if msg.text == msg.sender_nick:
        cur.execute("DELETE FROM user where name=(?)", (msg.sender_nick,))
        con.commit()
        return "Registration deleted."


@bot.regex_cmd_with_message(f"{NICK}: ")
def send_and_correct(_, msg):
    if not user_exists(msg.sender_nick):
        return "Please register with 'Register!'"

    formatted_msg = msg.text.replace(f"{NICK}: ", "")
    reply = instances[msg.sender_nick].send_message(
        prompts["answer"].format(
            language=get_user_language(msg.sender_nick),
            message=formatted_msg,
        )
    )["completion"]

    corrected_message = standalone_GPT.send_message(
        prompts["correction"].format(
            language=get_user_language(msg.sender_nick), message=formatted_msg
        )
    )["completion"]
    return [
        "-------- Correction --------",
        format_line_breaks(markdown_to_irc(corrected_message)),
        "-------- Response ----------",
        format_line_breaks(markdown_to_irc(reply)),
    ]


async def talk_to_user(user: str):
    global instances
    random_channel = random.choice([random.choice(CHANNELS), user])
    reply = instances[user].send_message(
        prompts["continue"].format(
            language=get_user_language(user),
        )
    )["completion"]
    reply = format_line_breaks(markdown_to_irc(reply))
    await bot.send_message(reply, random_channel)


@bot.regex_cmd_with_message("Register!", True)
async def register(_, message):
    if user_exists(message.sender_nick):
        return "Already registered."
    return ReplyIntent(
        Message(
            channel=message.sender_nick,
            sender_nick=message.sender_nick,
            message='Please define the language you wish to learn in ISO 369-1 format. Example: "de"',
        ),
        register_language,
    )


@bot.regex_cmd_with_message("Unregister!", True)
async def unregister(_, message):
    return ReplyIntent(
        Message(
            channel=message.sender_nick,
            sender_nick=message.sender_nick,
            message="Are you sure? Reply with your username to confirm.",
        ),
        confirm_dialog,
    )


@bot.regex_cmd_with_message("^Talk to me", True)
async def talk_to_me(_, message):
    if not user_exists(message.sender_nick):
        return "Not registered."
    await talk_to_user(message.sender_nick)


@bot.regex_cmd_with_message("^Change spam to", True)
async def change_spam(args, message):
    if not user_exists(message.sender_nick):
        return "Not registered."
    new_spam = message.text.split()[3]
    if new_spam.isdigit():
        if int(new_spam) in range(-1, 101):
            spam[message.sender_nick] = int(new_spam)
            cur.execute(
                "UPDATE user SET spam = ? WHERE name=?",
                (int(new_spam), message.sender_nick),
            )
            con.commit()
            return "Spam modified."
        else:
            return "Invalid spam number (not between 0 and 100)."
    else:
        return "Invalid input not a number."


@bot.regex_cmd_with_message("^(.*)$")
async def parse(_, message):
    global instances
    nick = message.sender_nick
    if user_exists(nick):
        if not instances.get(nick):
            instances[nick] = gpt.GPT()
        if not spam.get(nick):
            spam[nick] = get_user_spam(nick)


async def on_connect():
    while True:
        await asyncio.sleep(180)
        for user in spam:
            if spam[user] >= random.randint(1, 100):
                await talk_to_user(user)
                break


if __name__ == "__main__":
    bot.run(on_connect)
