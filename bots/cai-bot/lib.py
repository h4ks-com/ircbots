import asyncio
import logging
import os
import poplib
from contextlib import asynccontextmanager
from email.parser import Parser
from typing import AsyncIterator

from async_lru import alru_cache
from bs4 import BeautifulSoup
from characterai import aiocai, authUser, sendCode
from characterai.aiocai.client import WSConnect
from characterai.types.chat2 import BotAnswer, ChatData
from dotenv import load_dotenv

load_dotenv()

POP3_SERVER = os.getenv("POP3_SERVER")
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
PASSWORD = os.getenv("PASSWORD")
assert POP3_SERVER, "POP3_SERVER is required"
assert EMAIL_ADDRESS, "EMAIL_ADDRESS is required"
assert PASSWORD, "PASSWORD is required"


def get_token_from_email():
    pop_conn = poplib.POP3_SSL(POP3_SERVER)
    pop_conn.user(EMAIL_ADDRESS)
    pop_conn.pass_(PASSWORD)

    # Get the latest email
    num_messages = len(pop_conn.list()[1])
    response, raw_messages, octets = pop_conn.retr(num_messages)
    raw_message = b"\n".join(raw_messages)

    # Parse the email
    email_parser = Parser()
    msg = email_parser.parsestr(raw_message.decode("utf-8"))
    # subject = msg["subject"]

    url = None
    # Iterate over the parts of the email
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            html_content = part.get_payload(decode=True).decode(part.get_content_charset())
            soup = BeautifulSoup(html_content, "html.parser")
            links = soup.find_all("a")

            for link in links:
                url = link.get("href")

    pop_conn.quit()
    return url


@alru_cache
async def get_token() -> str:
    sendCode(EMAIL_ADDRESS)
    await asyncio.sleep(5)
    url = get_token_from_email()
    if not url:
        raise ValueError("Could not get token from email")

    token = authUser(url, EMAIL_ADDRESS)
    logging.info(f"Got token: {token}")
    return token


class ClientWrapper:
    def __init__(self, token: str):
        self.token = token
        self.aiocai = aiocai.Client(token=token)

    async def refresh_client(self) -> aiocai.Client:
        # drop cache for get_client
        get_token.cache_clear()
        self.token = await get_token()
        self.aiocai = aiocai.Client(token=self.token)
        return self.aiocai

    @asynccontextmanager
    async def new_chat(self, char_id: str) -> AsyncIterator[tuple[ChatData, BotAnswer, WSConnect]]:
        me = await self.aiocai.get_me()
        async with await self.aiocai.connect() as conn:
            new, answer = await conn.new_chat(char_id, str(me.id))
            yield new, answer, conn

    @asynccontextmanager
    async def open_chat(self) -> AsyncIterator[WSConnect]:
        async with await self.aiocai.connect() as conn:
            yield conn
