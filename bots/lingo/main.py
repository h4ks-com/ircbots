from typing import Dict
from ircbot import IrcBot, Message, ReplyIntent, TempData, message
from ircbot.format import format_line_breaks, markdown_to_irc

import iso639
import gpt
import json
import random
import asyncio
import os

from dotenv import load_dotenv
load_dotenv()

LOGFILE = None
HOST = os.getenv("IRC_HOST")
PORT = int(os.getenv("PORT") or 6697)
NICK = os.getenv("NICK") or "lingo"
PASSWORD = os.getenv("PASSWORD")
CHANNELS = json.loads(os.getenv("CHANNELS") or "[]")

import sqlite3
con = sqlite3.connect("user.db")
cur = con.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS user(name, language)")


standalone_GPT = gpt.GPT(contextualize = False)
instances : Dict[str, gpt.GPT] = {}# is habited by dicts where nicks correspond to a GPT object
spam : Dict[str, int]= {} # is habited by dicts of the chance of triggering a bot message same way as contexts

bot = IrcBot(
    HOST,
    PORT,
    NICK,
    CHANNELS,
    PASSWORD,
    use_ssl=True
)

prompts = {
    "initial_prompt": "Start a conversation with a user trying to learn a new language so he can learn it better, with a random topic, and write completely and and only in that language FOREVER. The language he is learning corresponds to the ISO 369-1 code ",
    "initial_prompt_without_first_message": "Start a conversation with a user trying to learn a new language so he can learn it better and write completely and and only in that language FOREVER. The language he is learning corresponds to the ISO 369-1 code",
    "correction_prompt": "Take the following message which is being used in a conversation with someone and reply with a very well written and explained syntax and meaning correction in the language written entirely and 100% with no other words that are NOT in the language that corresponds to the ISO 369-1 code "
}

def user_exists(user: str) -> bool:
    if cur.execute("SELECT * FROM user WHERE name=(?)", (user,)).fetchone():
        return True
    else:
        return False
def get_user_language(user: str) -> str:
    res = cur.execute("SELECT language from user WHERE name=(?)", (user,))
    return res.fetchone()[0]


def registerLanguage(msg):
    try:
        language = iso639.Language.from_part1(msg.text)
    except:
         return ReplyIntent("Invalid format. Please use ISO 369-1 format. Example: \"de\"", registerLanguage)
    user = (msg.sender_nick, msg.text)
    cur.execute("INSERT INTO user (name, language) VALUES (?,?)", user)
    con.commit()
    return "ight done"

def confirmDialog(msg):
    if msg.text == msg.sender_nick:
        cur.execute("DELETE FROM user where name=(?)", (msg.sender_nick,))
        con.commit()
        return "Donzo bozo"


@bot.regex_cmd_with_message(f"{NICK}: ")
def sendAndCorrectMessage(_, msg):
    if not user_exists(msg.sender_nick):
        return "Please register with \'Register!\'"
    formatted_msg = msg.text.replace(f"{NICK}: ", "") 
    if len(instances[msg.sender_nick].context) == 0:
        reply = instances[msg.sender_nick].send_message(prompts["initial_prompt_without_first_message"] + get_user_language(msg.sender_nick) + " : " + formatted_msg)["completion"]
    else:
        reply = instances[msg.sender_nick].send_message(formatted_msg)["completion"]
    corrected_message = standalone_GPT.send_message(prompts["correction_prompt"] + get_user_language(msg.sender_nick) + " : \"" + formatted_msg + "\"")["completion"]
    print(corrected_message)
    return ["Correction", format_line_breaks(markdown_to_irc(corrected_message)), "Reply", format_line_breaks(markdown_to_irc(reply))]

async def talk_to_user(user: str):
    global instances
    if len(instances[user].context) == 0:
        print("its here too")
        instances[user].send_message(prompts["initial_prompt"] + get_user_language(user))
    random_channel = random.choice([random.choice(CHANNELS), user])
    reply = instances[user].context[-1]["content"]
    await bot.send_message(reply, random_channel)
    # return ReplyIntent(Message(channel=random_channel, sender_nick=user, message=reply), sendAndCorrectMessage)


@bot.regex_cmd_with_message("Register!", True)
async def register(m, message):
    if user_exists(message.sender_nick):
        return "Already registered lol"
    return ReplyIntent(Message(channel=message.sender_nick, sender_nick=message.sender_nick, message = "Please define the language you wish to learn in ISO 369-1 format. Example: \"de\""), registerLanguage)

@bot.regex_cmd_with_message("Unregister!", True)
async def unregister(m, message):
    return ReplyIntent(Message(channel=message.sender_nick, sender_nick=message.sender_nick, message = "Are you sure? Reply with your username to confirm."), confirmDialog)

@bot.regex_cmd_with_message("^(.*)$")
async def parse(m, message):
    global instances
    nick = message.sender_nick
    if user_exists(nick):
        if not instances.get(nick):
            instances[nick] = gpt.GPT()
        if spam.get(nick):
            spam[nick] += 1
        else:
            spam[nick] = 0

async def on_connect():
    while True:
        await asyncio.sleep(300)
        for user in spam:
            if spam[user] >= random.randint(1, 100):
                await talk_to_user(user)
                break
        for user in spam:
            spam[user] = 0

if __name__ == "__main__":
    bot.run(on_connect)
