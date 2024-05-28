import json
import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv
from google.cloud.exceptions import ClientError
from ircbot import IrcBot, Message, ReplyIntent
from ircbot.format import format_line_breaks, markdown_to_irc
from lib import MAX_PROCESSING_SIZE, bq_process_size, bq_query, bq_schema, human_size

load_dotenv()

HOST = os.getenv("IRC_HOST")
assert HOST, "IRC_HOST is required"
PORT = int(os.getenv("IRC_PORT") or 6667)
SSL = os.getenv("IRC_SSL") == "true"
NICK = os.getenv("NICK") or "_bqbot"
PASSWORD = os.getenv("PASSWORD") or ""
CHANNELS = json.loads(os.getenv("CHANNELS") or "[]")


@dataclass
class UserData:
    accumulated_query: str
    line_count: int = 0


@dataclass
class BotData:
    accumulated_queries: dict[tuple[str, str], UserData]
    is_finished: bool = False

    def _prepare_msg(self, message: Message) -> tuple[str, str]:
        return message.channel.casefold(), message.nick.casefold()

    def initiated(self, message: Message) -> bool:
        return (
            self._prepare_msg(message) in self.accumulated_queries
            and not self.is_finished
        )

    def count_lines(self, message: Message) -> int:
        return self.accumulated_queries[self._prepare_msg(message)].line_count

    def append(self, message: Message, query: str) -> bool:
        if ";" in query:
            self.is_finished = True
        key = self._prepare_msg(message)
        if key not in self.accumulated_queries:
            self.accumulated_queries[key] = UserData("", 0)
        self.accumulated_queries[key].accumulated_query += query.split(";")[0] + "\n"
        self.accumulated_queries[key].line_count += 1
        return self.is_finished

    def flush(self, message: Message) -> str:
        if not self.is_finished:
            raise ValueError("Query is not finished. It should end with `;`")
        self.is_finished = False
        return self.accumulated_queries.pop(
            self._prepare_msg(message)
        ).accumulated_query


class BqBot(IrcBot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accumulated_queries = BotData({})


bot = (
    BqBot(HOST, nick=NICK, channels=CHANNELS, password=PASSWORD, use_ssl=SSL, port=PORT)
    .set_parser_order(False)
    .set_single_match(True)
)


async def safe_run_query(message: Message):
    query = bot.accumulated_queries.flush(message)
    try:
        size = bq_process_size(query)
        if size is None or size > MAX_PROCESSING_SIZE:
            await bot.reply(
                message,
                f"Sorry, this query is too expensive to run. Limit is {human_size(MAX_PROCESSING_SIZE)} "
                f" while query is {human_size(size) if size is not None else '?'}",
            )
            return

        df = bq_query(query)
    except ClientError as e:
        await bot.reply(message, ", ".join([err["message"] for err in e.errors]))
        return

    markdown_table = df.to_markdown(index=False) or "?"
    if markdown_table:
        await bot.reply(message, markdown_table.splitlines())


async def accumulate_query(message: Message):
    # Ignore other users
    if not bot.accumulated_queries.initiated(message):
        return

    bot.accumulated_queries.append(message, message.text)
    if not bot.accumulated_queries.is_finished:
        if bot.accumulated_queries.count_lines(message) % 10 == 0:
            await bot.reply(
                message,
                "I'm still waiting for the rest of the query. Please end it with `;`",
            )
        return ReplyIntent(None, accumulate_query)

    await safe_run_query(message)
    return ""


@bot.regex_cmd_with_messsage(rf"^\s*{re.escape(NICK)}:? (.+)$")
async def initiator(args: re.Match, message: Message):
    query = args[1]
    bot.accumulated_queries.append(message, query)
    if not bot.accumulated_queries.is_finished:
        await bot.reply(
            message,
            "I will be silently waiting for the rest of the query. Please end it with `;`",
        )
        return ReplyIntent(None, accumulate_query)
    await safe_run_query(message)


@bot.regex_cmd_with_messsage(rf"^\s*{re.escape(NICK)}:? `((?:[^`]|\S)+)`$")
async def bq_get_schema(args: re.Match, message: Message):
    table = args[1]
    try:
        description, schema = bq_schema(table)
    except ClientError as e:
        await bot.reply(message, ", ".join([err["message"] for err in e.errors]))
        return
    if description:
        await bot.reply(message, f"Description: {description}")
    await bot.reply(message, schema.to_markdown(index=False) or "?")


@bot.regex_cmd_with_messsage(rf"^\s*{re.escape(NICK)}:? help$")
async def help_message(args: re.Match, message: Message):
    body = f"""
Hi! I'm a bot that can run BigQuery queries. Here are some commands you can use:
- **{NICK}**: `<query>`: Run a BigQuery query. The query should end with `;`. You can do multi line queries.
- **{NICK}**: `<table_name>`: Get the schema of a BigQuery table.
- **{NICK}**: `help`: Show this help message.
    """
    await bot.reply(message, format_line_breaks(markdown_to_irc(body)))


if __name__ == "__main__":
    bot.run()
