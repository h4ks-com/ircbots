import asyncio
import json
import logging
import os
import poplib
from contextlib import asynccontextmanager
from email.parser import Parser
from typing import AsyncIterator

import requests
from async_lru import alru_cache
from bs4 import BeautifulSoup
from characterai import aiocai
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


URL = "https://beta.character.ai"
headers = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7,de;q=0.6",
    "access-control-request-headers": "content-type,x-client-version,x-firebase-gmpid",
    "access-control-request-method": "POST",
    "cache-control": "no-cache",
    "origin": "https://character.ai",
    "pragma": "no-cache",
    "priority": "u=1, i",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
}


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
            html_content = part.get_payload(decode=True).decode(
                part.get_content_charset()
            )
            soup = BeautifulSoup(html_content, "html.parser")
            links = soup.find_all("a")

            for link in links:
                url = link.get("href")

    pop_conn.quit()
    return url


def sendCode(email: str) -> bool:
    r = requests.post(
        "https://identitytoolkit.googleapis.com"
        "/v1/accounts:sendOobCode?key="
        "AIzaSyAbLy_s6hJqVNr2ZN0UHHiCbJX1X8smTws",
        json={
            "requestType": "EMAIL_SIGNIN",
            "email": email,
            "clientType": "CLIENT_TYPE_WEB",
            "continueUrl": "https://beta.character.ai",
            "canHandleCodeInApp": True,
        },
        headers=headers,
        params={"key": "AIzaSyAbLy_s6hJqVNr2ZN0UHHiCbJX1X8smTws"},
    )

    try:
        data = r.json()
    except json.decoder.JSONDecodeError:
        logging.error(f"Could not decode JSON: {r.text}")
        return False

    try:
        if data["email"] == email:
            return True
    except KeyError:
        raise ValueError(data["error"]["message"])
    return False


def authUser(link: str, email: str) -> str:
    r = requests.get(link, allow_redirects=True)

    oobCode = r.url.split("oobCode=")[1].split("&")[0]

    r = requests.post(
        "https://identitytoolkit.googleapis.com"
        "/v1/accounts:signInWithEmailLink?key="
        "AIzaSyAbLy_s6hJqVNr2ZN0UHHiCbJX1X8smTws",
        headers={
            # Firebase key for GoogleAuth API
            "X-Firebase-AppCheck": "eyJraWQiOiJYcEhKU0EiLCJ"
            "0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIx"
            "OjQ1ODc5NzcyMDY3NDp3ZWI6YjMzNGNhNDM2MWU5MzRkYWV"
            "iOWQzYiIsImF1ZCI6WyJwcm9qZWN0c1wvNDU4Nzk3NzIwNjc"
            "0IiwicHJvamVjdHNcL2NoYXJhY3Rlci1haSJdLCJwcm92aWR"
            "lciI6InJlY2FwdGNoYV9lbnRlcnByaXNlIiwiaXNzIjoiaHR0"
            "cHM6XC9cL2ZpcmViYXNlYXBwY2hlY2suZ29vZ2xlYXBpcy5jb"
            "21cLzQ1ODc5NzcyMDY3NCIsImV4cCI6MTcxMTAxNzE2MiwiaWF"
            "0IjoxNzEwNDEyMzYyLCJqdGkiOiJkSXlkWVFPZEhnaTRmc2ZGU"
            "DMtWHNZVU0zZG01eFY4R05ncDItOWxCQ2xVIn0.o2g6-5Pl7rj"
            "iKdQ4X9bdOe6tDSVmdODFZUljHDnF5cNCik6masItwpeL3Yh6h"
            "78sQKNwuKzCUBFjsvDsEIdu71gW4lAuDxhKxljffX9nRuh8d0j-"
            "ofmwq_4abpA3LdY12gIibvMigf3ncBQiJzu4SVQUKEdO810oUG8"
            "G4RWlQfBIo-PpCO8jhyGZ0sjcklibEObq_4-ynMZnhTuIN_J183"
            "-RibxiKMjMTVaCcb1XfPxXi-zFr2NFVhSM1oTWSYmhseQ219ppH"
            "A_-cQQIH6MwC0haHDsAAntjQkjbnG2HhPQrigdbeiXfpMGHAxLR"
            "XXsgaPuEkjYFUPoIfIITgvkj5iJ-33vji2NgmDCpCmpxpx5wTHOC"
            "8OEZqSoCyi3mOkJNXTxOHmxvS-5glMrcgoipVJ3Clr-pes3-aI5Y"
            "w7n3kmd4YfsKTadYuE8vyosq_MplEQKolRKj67CSNTsdt2fOsLCW"
            "Nohduup6qJrUroUpN35R9JuUWgSy7Y4MI6NM-bKJ"
        },
        json={"email": email, "oobCode": oobCode},
    )

    data = r.json()

    try:
        idToken = data["idToken"]
    except KeyError:
        raise ValueError(data["error"]["message"])

    r = requests.post(f"{URL}/dj-rest-auth/google_idp/", json={"id_token": idToken})

    data = r.json()

    try:
        return data["key"]
    except KeyError:
        raise ValueError(data["error"])


@alru_cache
async def get_token() -> str:
    if not sendCode(EMAIL_ADDRESS):
        raise ValueError("Could not send code")
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
    async def new_chat(
        self, char_id: str
    ) -> AsyncIterator[tuple[ChatData, BotAnswer, WSConnect]]:
        me = await self.aiocai.get_me()
        async with await self.aiocai.connect() as conn:
            new, answer = await conn.new_chat(char_id, str(me.id))
            yield new, answer, conn

    @asynccontextmanager
    async def open_chat(self) -> AsyncIterator[WSConnect]:
        async with await self.aiocai.connect() as conn:
            yield conn
