#########################################################################
#  Matheus Fillipe -- 22, April of 2021                                 #
#                                                                       #
#########################################################################
#  Description: A simple chess game that allows players in different    #
#  channels to have multiple games at the same time and also provides   #
#  a cpu player using stockfish.                                        #
#                                                                       #
#########################################################################

import json
import logging
import os
import re
import shutil
from copy import copy, deepcopy
from datetime import datetime

import chess
import chess.engine
from dotenv import load_dotenv
from ircbot import IrcBot, utils
from ircbot.client import PersistentData
from ircbot.format import Color
from ircbot.message import Message
from ircbot.utils import debug, log

load_dotenv()

##################################################
# SETTINGS                                       #
##################################################

LOGFILE = None
LEVEL = logging.DEBUG
HOST = os.getenv("IRC_HOST") or "irc.dot.org.es"
SSL = os.getenv("IRC_SSL") == "true"
PORT = int(os.getenv("IRC_PORT") or 6667)
NICK = os.getenv("NICK") or "chessbot1"
PASSWORD = os.getenv("PASSWORD") or ""
CHANNELS = json.loads(os.getenv("CHANNELS") or '["#test"]')
PREFIX = ";"
# Find stockfish binary in common locations
STOCKFISH_PATHS = [
    shutil.which("stockfish"),
    "/usr/bin/stockfish",
    "/usr/games/stockfish",
]

STOCKFISH = None
for path in STOCKFISH_PATHS:
    if path and os.path.exists(path):
        STOCKFISH = path
        break

if not STOCKFISH:
    raise FileNotFoundError(
        "Stockfish engine not found. Please install stockfish or set the path manually."
    )

TIME_TO_THINK = 0.05
EXPIRE_INVITE_IN = 60  # secods
EXPIRE_REQUEST_TIME = 15
DEFAULT_PREF = {
    "fg": [Color.white, Color.black],
    "bg": [Color.maroon, Color.gray],
    "label": "   A  B  C  D  E  F  G  H   ",
    "bmode": "normal",
}
DB_PATH = os.getenv("DB_PATH") or "."
os.makedirs(DB_PATH, exist_ok=True)
ONGOING_GAMES_STORE = os.path.join(DB_PATH, f"{NICK}_ongoing_games.json")

############################################################

### Data permanency
db_columns = ["nick", "checkmates", "stalemates", "draws", "games", "losses", "prefs"]
db_handler = PersistentData(os.path.join(DB_PATH, NICK + ".db"), "users", db_columns)

# Initialize bot
bot = IrcBot(HOST, PORT, NICK, CHANNELS, PASSWORD, use_ssl=SSL, tables=[db_handler])
utils.set_loglevel(LEVEL)
bot.set_prefix(PREFIX).set_help_on_private(True).set_simplify_commands(False)


def get_data(nick):
    for user in db_handler.data:
        if nick == user[db_columns[0]]:
            return user


def create_data(nick):
    default_data = {
        "nick": nick,
        "checkmates": 0,
        "stalemates": 0,
        "draws": 0,
        "games": 0,
        "losses": 0,
        "prefs": json.dumps(DEFAULT_PREF),
        "ongoing_games": "{}",  # {channel: against_nick: [b2b4,c3c6..]}
    }
    db_handler.push(default_data)
    return default_data


def save_ongoing_games(data):
    debug("Reading ongoing games json")
    with open(ONGOING_GAMES_STORE, "w") as outfile:
        json.dump(data, outfile)


def load_ongoing_games():
    debug("Trying to read ongoing games json")
    data = {}
    try:
        with open(ONGOING_GAMES_STORE) as json_file:
            data = json.load(json_file)
    except FileNotFoundError:
        debug("Read failed. Creating new file json")
        save_ongoing_games({})
        return load_ongoing_games()
    except json.decoder.JSONDecodeError:
        log("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        log("Recreating json store. Reason: File is corrupt")
        data = {}
        with open(ONGOING_GAMES_STORE, "w") as outfile:
            json.dump(data, outfile)
    return data


def add_game(nick, against_nick, channel):
    ongoing_games = load_ongoing_games()
    if channel in ongoing_games and against_nick in ongoing_games[channel]:
        return
    if nick not in ongoing_games:
        ongoing_games[nick] = {}
    if channel not in ongoing_games:
        ongoing_games[nick][channel] = {}
    ongoing_games[nick][channel][against_nick] = []
    save_ongoing_games(ongoing_games)
    debug("Created ongoing game Data!!!!!")
    return True


def update_game(nick, against_nick, channel, game, nocheck=False):
    ongoing_games = load_ongoing_games()
    debug(f"Searching for {nick=} {channel=} {ongoing_games=} ")
    if (
        nick in ongoing_games
        and channel in ongoing_games[nick]
        and against_nick in ongoing_games[nick][channel]
    ):
        ongoing_games[nick][channel][against_nick] = game.history
        save_ongoing_games(ongoing_games)
        debug("Updated ongoing game Data!!!!!")
        return True
    if nocheck:
        return
    update_game(game.other(nick), nick, channel, game, nocheck=True)


def delete_game(nick, against_nick, channel, game, nocheck=False):
    ongoing_games = load_ongoing_games()
    if (
        nick in ongoing_games
        and channel in ongoing_games[nick]
        and against_nick in ongoing_games[nick][channel]
    ):
        del ongoing_games[nick][channel][against_nick]
        save_ongoing_games(ongoing_games)
        debug("Removed ongoing game Data!!!!!")
        return True
    if nocheck:
        return
    delete_game(game.other(nick), nick, channel, game, nocheck=True)


def increment_data(nick, column):
    data = get_data(nick)
    if data is None:
        data = {
            "nick": nick,
            "checkmates": 0,
            "stalemates": 0,
            "draws": 0,
            "games": 0,
            "losses": 0,
            "prefs": json.dumps(DEFAULT_PREF),
            "ongoing_games": "{}",  # {channel: against_nick: [b2b4,c3c6..]}
        }
        data.update({column: 1})
        log("Creating player data")
        db_handler.push(data)
        return data
    else:
        log("incrementing player data")
        data.update({column: data[column] + 1})
        db_handler.update(data["id"], data)


def update_data(nick, data):
    for user in db_handler.data:
        if nick == user[db_columns[0]]:
            user.update(data)
            db_handler.update(user["id"], user)
            return True


####################################################################################
# Player logics

remap = {
    "R": "♜",
    "N": "♞",
    "B": "♝",
    "Q": "♛",
    "K": "♚",
    "P": "♟︎",
    ".": "♟︎",
}

engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH)


def cpuPlay(board: chess.Board):
    global engine
    result = engine.play(board, chess.engine.Limit(time=TIME_TO_THINK))
    board.push(result.move)
    return result.move.uci()


class Game:
    BG_CLASSIC = [Color.maroon, Color.gray]
    FG_CLASSIC = [Color.white, Color.black]
    BG_MODERN = [Color.purple, Color.red]
    FG_MODERN = [Color.white, Color.yellow]
    BMODES = {
        "normal": {"pieces": [1, 1], "pawns": [1, 1], "remap": {}},
        "wide": {
            "remap": {".": "♟", "P": "♟"},
            "pieces": [2, 2],
            "pawns": [2, 2],
            "prefs": {"label": "    A     B     C    D     E     F     G     H"},
        },
        "erc": {
            "remap": {".": "♟", "P": "♟"},
            "pieces": [1, 1],
            "pawns": [1, 1],
            "prefs": {"label": "   A  B  C  D  E  F  G  H"},
        },
    }

    LABEL = [
        "   A  B  C  D  E  F  G  H   ",
        "     A   B   C   D   E   F   G   H  ",
        "    A     B     C    D     E     F     G     H",
    ]

    PREF = DEFAULT_PREF

    def __init__(self, p1, p2):
        self.board = chess.Board()
        self.player = False
        self.nicks = [p1, p2]
        self.p1 = p1
        self.p2 = p2
        self.prefs = {
            self.p1: Game.PREF,
            self.p2: Game.PREF,
        }
        self.history = []

    def loadprefs(self):
        pref1 = get_data(self.p1)
        pref2 = get_data(self.p2)
        pref1 = pref1["prefs"] if pref1 else None
        pref2 = pref2["prefs"] if pref2 else None
        self.prefs = {
            self.p1: json.loads(pref1) if pref1 else Game.PREF,
            self.p2: json.loads(pref2) if pref2 else Game.PREF,
        }

    def who(self):
        return self.nicks[self.player]

    def pop(self):
        self.board.pop()
        self.player = not self.player
        self.history.pop()

    def move(self, uic):
        self.board.push_uci(uic)
        self.player = not self.player
        self.history.append(uic)

    def other(self, nick):
        return self.p1 if nick == self.p2 else self.p2

    def utf8_board(self, nick):
        self.loadprefs()
        R = []
        bgi = 1
        row = 8
        label = self.prefs[nick]["label"]
        BG = self.prefs[nick]["bg"]
        FG = self.prefs[nick]["fg"]
        bmode = self.prefs[nick]["bmode"]
        layout = (
            self.BMODES["normal"] if bmode not in self.BMODES else self.BMODES[bmode]
        )
        b_map = deepcopy(remap)
        b_map.update(layout.get("remap"))

        # Create colored UTF8 board from ASCII board
        R.append(label)
        for line in str(self.board).split("\n"):
            colors = []
            for c in line:
                if c.upper() not in b_map:
                    continue
                piece_type = "pawns" if b_map[c.upper()] == b_map["P"] else "pieces"
                spacing = layout[piece_type]
                if c.upper() == c:
                    piece = Color(
                        f"{' '*spacing[0]}{b_map[c.upper()]}{' '*spacing[1]}",
                        bg=BG[bgi],
                        fg=FG[0] if c != "." else BG[bgi],
                    )
                else:
                    piece = Color(
                        f"{' '*spacing[0]}{b_map[c.upper()]}{' '*spacing[1]}",
                        bg=BG[bgi],
                        fg=FG[1] if c != "." else BG[bgi],
                    )
                colors.append(piece)
                bgi = not bgi
            bgi = not bgi
            colors[-1].str = colors[-1].str[:-1]
            sep = " \003 "
            R.append(str(row) + " " + "".join([c.str for c in colors]) + sep + str(row))
            row -= 1
        R.append(label)
        return R


def set_prefs(nick, **kwargs):
    """set_prefs.

    :param nick:
    :param kwargs: fg, bg, label
    """
    for user in db_handler.data:
        if nick == user[db_columns[0]]:
            data = json.loads(user.get("prefs"))
            data.update(kwargs)
            user.update({"prefs": json.dumps(data)})
            db_handler.update(user["id"], user)
            return True
    default = Game.PREF
    default.update(kwargs)
    db_handler.push(
        {
            "nick": nick,
            "checkmates": 0,
            "stalemates": 0,
            "draws": 0,
            "games": 0,
            "losses": 0,
            "prefs": json.dumps(default),
        }
    )
    return False


class BotState:
    def __init__(self):
        self.games = {}  # {nick: {channel: {selected: index, games: [game1, game2]},..}, ...}
        self.invites = {}  # {nick: {channel: {nick1: time,  nick2: time },channel2 ...}}
        self.undo_requests = {}  # {nick: {channel: {nick: time}}}

    def request_undo(self, nick, against_nick, channel):
        if not self.has_game_with(nick, against_nick, channel):
            return
        if against_nick not in self.undo_requests:
            self.undo_requests[against_nick] = {}
        if channel not in self.undo_requests[against_nick]:
            self.undo_requests[against_nick][channel] = {}
        self.undo_requests[against_nick][channel][nick] = datetime.now().timestamp()
        return True

    def undo(self, nick, against_nick, channel):
        game = self.has_game_with(nick, against_nick, channel)
        if not game:
            return

        if (
            against_nick in self.undo_requests
            and channel in self.undo_requests[against_nick]
            and nick in self.undo_requests[against_nick][channel]
        ):
            time = self.undo_requests[against_nick][channel][nick]
            del self.undo_requests[against_nick][channel][nick]
            if datetime.now().timestamp() - time < EXPIRE_REQUEST_TIME:
                return True
            return False

    def has_invited(self, nick, against_nick, channel):
        if against_nick in self.invites and channel in self.invites[against_nick]:
            if nick in self.invites[against_nick][channel]:
                return True
        return None

    def invite(self, nick, against_nick, channel):
        if self.has_invited(nick, against_nick, channel):
            return None
        if against_nick not in self.invites:
            self.invites[against_nick] = {}
        if channel not in self.invites[against_nick]:
            self.invites[against_nick][channel] = {}
        self.invites[against_nick][channel][nick] = datetime.now().timestamp()
        return True

    def remove_invite(self, nick, against_nick, channel):
        if self.has_invited(nick, against_nick, channel):
            self.invites[against_nick][channel].pop(nick)
            return True
        return None

    def _add_game(self, nick, against_nick, channel, new_game):
        if nick not in self.games:
            self.games[nick] = {}
        if channel not in self.games[nick]:
            self.games[nick][channel] = {"selected": 0, "games": []}
        self.games[nick][channel]["games"].append(new_game)
        self.games[nick][channel][
            "selected"
        ] = -1  # len(self.games[nick][channel]['games']) - 1

    def add_game(self, nick, against_nick, channel, check_invitation=True):
        if check_invitation and (
            against_nick != NICK and not self.remove_invite(nick, against_nick, channel)
        ):
            return None
        new_game = Game(nick, against_nick)
        self._add_game(nick, against_nick, channel, new_game)
        self._add_game(against_nick, nick, channel, new_game)
        return new_game

    def has_any_game(self, nick, channel):
        return (
            nick in self.games
            and channel in self.games[nick]
            and len(self.games[nick][channel]["games"]) > 0
        )

    def has_game_with(self, nick, against_nick, channel):
        if not self.has_any_game(nick, channel):
            return None
        for game in self.games[nick][channel]["games"]:
            if nick in [game.p1, game.p2] and game.other(nick) == against_nick:
                return game
        return None

    def get_games(self, nick, channel=None):
        if channel and self.has_any_game(nick, channel):
            return self.games[nick][channel]["games"]
        if not channel and nick in self.games:
            games = []
            for chan in self.games[nick]:
                games += self.games[nick][chan]["games"]
            return games
        return None

    def get_selected_game(self, nick, channel):
        if not self.has_any_game(nick, channel):
            return None
        games = self.games[nick][channel]
        return games["games"][games["selected"]]

    def select_game(self, nick, against_nick, channel):
        games = self.get_games(nick, channel)
        if games:
            for game in games:
                if game.other(nick) == against_nick:
                    self.games[nick][channel]["selected"] = self.games[nick][channel][
                        "games"
                    ].index(game)
                    return game
        return None

    def end_game(self, nick, against_nick, channel):
        game: Game = self.has_game_with(nick, against_nick, channel)
        if game is None:
            return None
        if (
            self.games[nick][channel]["games"][self.games[nick][channel]["selected"]]
            == game
        ):
            self.games[nick][channel]["selected"] = -1
        self.games[nick][channel]["games"].remove(game)
        self.end_game(against_nick, nick, channel)
        return True


# IRC Bot commands

botState = BotState()


def print_board(args, message: Message, notsave=False):
    nick = message.nick
    if nick not in botState.games:
        return "You aren't currently in any game. Type start [nick] to start a new game with a player"

    game = botState.get_selected_game(nick, message.channel)
    if game:
        return game.utf8_board(nick)
    return "You don't have any game selected on this channel"


async def start(bot, args, message):
    names = await bot.list_names(message.channel)
    nick = message.nick
    if not args[1]:
        return f"<{message.nick}> Usage: {PREFIX}start [nick] or start {NICK} to play against the cpu."
    if args[1] not in names:
        return f"The name {args[1]} is not on this channel or is invalid"
    if args[1] == message.nick:
        return f"<{args[1]}> Play with yourself using your own imagination!"
    if botState.has_invited(message.nick, args[1], message.channel):
        return f"<{message.nick}> You already invited {args[1]} for a game."
    if botState.has_game_with(message.nick, args[1], message.channel):
        return (
            f"<{message.nick}> You are already in a game with {args[1]} on this channel"
        )
    if args[1] == NICK:
        game = botState.add_game(message.nick, args[1], message.channel)
        increment_data(game.p1, "games")
        increment_data(game.p2, "games")
        add_game(game.p1, game.p2, message.channel)
        return ["Starting CPU game"] + game.utf8_board(nick)

    botState.invite(nick, args[1], message.channel)
    return f"<{args[1]}> {nick} is challenging you to a chess game. Use `{PREFIX}accept {nick}` to accept within the next {EXPIRE_INVITE_IN} seconds."


def accept(args, message):
    if not args[1]:
        return f"<{message.nick}> Usage: {PREFIX}accept [nick]"
    game = botState.add_game(args[1], message.nick, message.channel)
    if game is None:
        return f"<{message.nick}> You have no invitation from {args[1]} on this channel"

    increment_data(game.p1, "games")
    increment_data(game.p2, "games")
    add_game(game.p1, game.p2, message.channel)
    return [f"<{message.nick}> Starting game with {args[1]}"] + game.utf8_board(game.p1)


def score(args, message):
    nick = args[1] if args[1] else message.nick
    data = copy(get_data(nick))
    if data is None:
        return (
            f"<{message.nick}> I don't know nothing about you yet...."
            if not args[1]
            else f"<{message.nick}> I don't know nothing about {nick} yet...."
        )
    data.pop("prefs")
    data.pop("id")
    data.pop("nick")
    data["unfinished"] = (
        data["games"]
        - data["checkmates"]
        - data["stalemates"]
        - data["draws"]
        - data["losses"]
    )
    return f"<{nick}> has: {', '.join([f'{n}: {v}' for n,v in data.items()])}"


def getMoves(uic, board):
    possible_moves = [m.uci() for m in list(board.legal_moves)]
    r_moves = []
    for mov in possible_moves:
        if mov.startswith(uic.lower()):
            r_moves.append(mov)
    return r_moves


async def move(bot, args, message):
    try:
        uci = chess.Move.from_uci(args[1])
    except ValueError:
        uci = None

    game: Game = botState.get_selected_game(message.nick, message.channel)
    chan_names = await bot.list_names(message.channel)
    if game is None:
        return f"<{message.nick}> You don't have any game selected"
    if game.nicks[game.player] != message.nick:
        return f"<{message.nick}> It is not your move!"
    board = game.board
    if uci not in board.legal_moves:
        if args[1] and len(args[1]) == 2 and re.match(r"^[a-h][1-8]$", args[1]):
            p_moves = getMoves(args[1], board)
            if len(p_moves) == 0:
                return f"<{message.nick}> There are no possible moves for {args[1]}"
            return (
                f"<{message.nick}> possible moves for {args[1].lower()} are: "
                + ", ".join(p_moves)
            )
        return f"<{message.nick}> Invalid move! Use uic moves like e2e4, c8c4, a7a8q, etc..."

    game.move(args[1])
    if game.nicks[game.player] != NICK:
        update_game(game.p1, game.p2, message.channel, game)

    def endGame(msg, nick):
        boards = game.utf8_board(game.p1) + game.utf8_board(game.p2)
        botState.end_game(nick, game.nicks[game.player], message.channel)
        return [msg] + boards + [f"{nick} wins"]

    def checkBoard(nick=None):
        if board.is_game_over():
            nick = nick if nick is not None else message.nick
            against_nick = game.nicks[game.player]
            if board.is_variant_draw():
                increment_data(game.p1, "draws")
                increment_data(game.p2, "draws")
                delete_game(game.p1, game.p2, message.channel, game)
                return endGame("DRAW!", nick)
            if board.is_stalemate():
                increment_data(nick, "stalemates")
                increment_data(against_nick, "losses")
                delete_game(game.p1, game.p2, message.channel, game)
                return endGame("STALEMATE!", nick)
            if board.is_checkmate():
                increment_data(nick, "checkmates")
                increment_data(against_nick, "losses")
                delete_game(game.p1, game.p2, message.channel, game)
                return endGame("CHECKMATE!", nick)
            increment_data(nick, "checkmates")
            increment_data(against_nick, "losses")
            delete_game(game.p1, game.p2, message.channel, game)
            boards = game.utf8_board(game.p1) + game.utf8_board(game.p2)
            botState.end_game(nick, against_nick, message.channel)
            return ["END..."] + boards

    end = checkBoard()
    if end:
        return end

    if game.nicks[game.player] == NICK:
        if board.is_check():
            b1 = ["CHECK"]
            b1 += game.utf8_board(game.nicks[game.player])
        else:
            b1 = game.utf8_board(game.nicks[game.player])

        uic = cpuPlay(game.board)
        game.player = not game.player
        game.history.append(uic)
        update_game(game.p1, game.p2, message.channel, game)
        end = checkBoard(NICK)
        if end:
            return end

    if board.is_check():
        return (
            ["CHECK"]
            + [f"It is {game.who()}'s turn!"]
            + [game.utf8_board(game.nicks[game.player])]
        )

    if game.who() in chan_names:
        return [f"It is {game.who()}'s turn!"] + game.utf8_board(
            game.nicks[game.player]
        )
    else:
        return f"Do not worry {message.nick}; you can still move. {game.who()} will be notified when he is back!"


def label(args, message):
    if not args[1] or not args[1].isdigit():
        return f"<{message.nick}> Usage: {PREFIX}label [1|2]"
    n = int(args[1])
    if n > len(Game.LABEL) or n <= 0:
        return [
            f"<{message.nick}> There are only {len(Game.LABEL)} possible labels. Usage: {PREFIX}label [1|2]"
        ] + [f"{i+1}: {label}" for i, label in enumerate(Game.LABEL)]

    set_prefs(message.nick, label=Game.LABEL[n - 1])
    game: Game = botState.get_selected_game(message.nick, message.channel)
    return (
        ["The label preference was set!"] + game.utf8_board(message.nick)
        if game
        else []
    )


def colors(args, message):
    av_colors_str = f"<{message.nick}> The available colors are: " + ", ".join(
        Color.colors()
    )
    pieces_str = "pieces"
    board_str = "board"
    usage_str = (
        f"<{message.nick}> Usage: colors [{pieces_str}|{board_str}] [color1] [color2]"
    )
    if not args[1]:
        return av_colors_str
    args = [args[i] for i in range(1, 4)]
    a = False

    prefs = get_data(message.nick)
    prefs = json.loads(prefs["prefs"]) if prefs else DEFAULT_PREF
    FG = prefs["fg"]
    BG = prefs["bg"]

    if args[0] == "classic":
        BG = [Color.maroon, Color.gray]
        FG = [Color.white, Color.black]
        a = True
    if args[0] == "modern":
        BG = [Color.purple, Color.red]
        FG = [Color.white, Color.yellow]
        a = True
    if not a:
        if len(args) != 3:
            return usage_str
        elm, c1, c2 = args
        if c1 not in Color.colors() or c2 not in Color.colors():
            return [f"({message.nick}) Invalid colors!", av_colors_str]
        if elm == pieces_str:
            FG = [getattr(Color, c) for c in [c1, c2]]
        elif elm == board_str:
            BG = [getattr(Color, c) for c in [c2, c1]]
        else:
            return usage_str

    set_prefs(message.nick, fg=FG, bg=BG)
    game: Game = botState.get_selected_game(message.nick, message.channel)
    return (
        ["The color preference was set!"] + game.utf8_board(message.nick)
        if game
        else []
    )


def hint(args, message):
    game: Game = botState.get_selected_game(message.nick, message.channel)
    if game is None:
        return f"<{message.nick}> You don't have any game selected"
    board = game.board
    if args[1] and len(args[1]) == 2:
        return (
            f"({message.nick}) possible moves for {args[1].lower()} are: "
            + ", ".join(getMoves(args[1], board))
        )
    if args[1]:
        return "Pass in a board position like: hint e2, or no arguments to see all possible moves"
    possible_moves = [m.uci() for m in list(board.legal_moves)]
    return f"({message.nick}) possible moves are: " + ", ".join(possible_moves)


def undo(args, message):
    game: Game = botState.get_selected_game(message.nick, message.channel)
    against_nick = game.p2 if game.p1 == message.nick else game.p1
    if game is None:
        return f"<{message.nick}> You don't have any game selected"

    undo_result = botState.undo(against_nick, message.nick, message.channel)
    if undo_result:
        game.pop()
        update_game(game.p1, game.p2, message.channel, game)
        return [
            f"<{message.nick}> it is {game.nicks[game.player]}'s move"
        ] + game.utf8_board(message.nick)

    if undo_result is False:
        return f"<{message.nick}> The undo request has expired. Repeat the command if you want to undo again."

    if against_nick == NICK:
        game.pop()
        game.pop()
        update_game(game.p1, game.p2, message.channel, game)
        return [
            f"<{message.nick}> it is {game.nicks[game.player]}'s move"
        ] + game.utf8_board(message.nick)

    if game.who() != game.other(message.nick):
        return f"<{message.nick}> you can only undo when it is not your turn (after your last move)"

    if botState.request_undo(message.nick, against_nick, message.channel):
        return f"<{game.other(message.nick)}> {message.nick} is asking you to undo the last movement. Use `{PREFIX}undo` to accept this action within the next {EXPIRE_REQUEST_TIME} seconds."

    return f"<{message.nick}> You can't undo now"


def bmode(args, message):
    if args[1] in Game.BMODES:
        if "prefs" in Game.BMODES[args[1]]:
            set_prefs(message.nick, bmode=args[1], **Game.BMODES[args[1]]["prefs"])
        else:
            set_prefs(message.nick, bmode=args[1])
        game: Game = botState.get_selected_game(message.nick, message.channel)
        msg = f"<{message.nick}> {args[1]} mode set!"
        return [msg] + game.utf8_board(message.nick) if game else msg

    return f"<{message.nick}> This is not a valid option. The only valid options for bmode currently {'is' if len(Game.BMODES) <2 else 'are'}: {', '.join(['`' + key + '`' for key in Game.BMODES.keys()])}"


# Commands registered with decorators below


@bot.arg_command("end", "Ends selected game", f"{PREFIX}end or {PREFIX}end [nick]")
def end(args, message):
    if not args[1]:
        game = botState.get_selected_game(message.sender_nick, message.channel)
    else:
        game = botState.has_game_with(message.sender_nick, args[1], message.channel)
    if not game:
        return f"<{message.sender_nick}> You don't have any game with {args[1]}"
    if botState.end_game(
        message.sender_nick, game.other(message.sender_nick), message.channel
    ):
        delete_game(game.p1, game.p2, message.channel, game)
        return f"<{message.sender_nick}> Game cancelled!"
    return f"<{message.sender_nick}> Could not remove game {game.p1} vs {game.p2}"


@bot.arg_command(
    "forfeit", "Forfeits selected game", f"{PREFIX}forfeit or {PREFIX}forfeit [nick]"
)
def forfeit(args, message):
    if not args[1]:
        game = botState.get_selected_game(message.sender_nick, message.channel)
    else:
        game = botState.has_game_with(message.sender_nick, args[1], message.channel)
    if not game:
        return f"<{message.sender_nick}> You don't have any game with {args[1]}"

    is_onwer = game.p1 == message.sender_nick
    against_nick = game.other(message.sender_nick)
    if botState.end_game(
        message.sender_nick, game.other(message.sender_nick), message.channel
    ):
        if is_onwer:
            increment_data(message.sender_nick, "losses")
            increment_data(against_nick, "stalemates")
            delete_game(message.sender_nick, against_nick, message.channel, game)
        else:
            increment_data(message.sender_nick, "losses")
            increment_data(against_nick, "stalemates")
            delete_game(against_nick, message.sender_nick, message.channel, game)
        return f"<{against_nick}> {message.sender_nick} forfeits, you win!"
    return f"<{message.sender_nick}> Could not remove game {game.p1} vs {game.p2}"


@bot.arg_command("who", "Whose move is it", "")
def who(args, message):
    game = botState.get_selected_game(message.sender_nick, message.channel)
    if game:
        return f"<{message.sender_nick}> it is {game.nicks[game.player]}'s move"
    return f"<{message.sender_nick}> You are not on any game"


@bot.arg_command("invitations", "Check if someone started a game with you", "")
def invites(args, message):
    if (
        message.sender_nick not in botState.invites
        and message.channel in botState.invites[message.sender_nick][message.channel]
    ):
        return f"<{message.sender_nick}> You don't have any game invitation here"
    names = list(botState.invites[message.sender_nick][message.channel].keys())
    return f"<{message.sender_nick}> You have game invitations from: " + ", ".join(
        names
    )


@bot.arg_command("history", "Display history of moves", "")
def history(args, message):
    game = botState.get_selected_game(message.sender_nick, message.channel)
    if game:
        max_hist = 50
        chunks = [
            game.history[i : i + max_hist]
            for i in range(0, len(game.history), max_hist)
        ]
        if len(chunks) == 0:
            chunks = [""]
        return [
            f"<{message.sender_nick}> {', '.join([str(m) for m in hist])}"
            for hist in chunks
        ]
    return f"<{message.sender_nick}> You are not on any game"


@bot.arg_command("names", "Lists users on this room", "")
async def list_names(args, message):
    names = await bot.list_names(message.channel)
    return " ".join(names)


@bot.arg_command("games", "Current games", "Displays your current games.")
def games(args, message):
    if args[1]:
        if not botState.has_any_game(args[1], message.channel):
            return f"<{message.sender_nick}> I found no games for that user."
        games = botState.get_games(args[1], message.channel)
        return f"<{message.sender_nick}> " + ", ".join(
            [g.p1 if g.p2 == message.sender_nick else g.p2 for g in games]
        )
    if not botState.has_any_game(message.sender_nick, message.channel):
        return f"<{message.sender_nick}> You don't have any game on this channel."
    games = botState.get_games(message.sender_nick, message.channel)
    return (
        f"<{message.sender_nick}> Your games on this channel are against: "
        + ", ".join([g.p1 if g.p2 == message.sender_nick else g.p2 for g in games])
    )


@bot.arg_command("select", "Select/change between games", f"{PREFIX}select [nick]")
def select(args, message):
    if not args[1]:
        return f"<{message.sender_nick}> Usage: {PREFIX}select [nick]"
    if not botState.has_game_with(message.sender_nick, args[1], message.channel):
        return f"<{message.sender_nick}> You do not have any game with {args[1]}"
    game = botState.select_game(message.sender_nick, args[1], message.channel)
    return game.utf8_board(message.sender_nick)


@bot.custom_handler(["part", "quit"])
async def onQuit(nick, channel=None, text=""):
    async def notifyPlayers(chan):
        games = botState.get_games(nick, chan)
        players = [g.other(nick) for g in games]
        if NICK in players:
            players.remove(NICK)
        if len(players) > 0:
            await bot.send_message(
                f"Do not worry {', '.join(players)}; you can still move. {nick} will be notified when he is back!"
            )

    if channel is None:  # quit
        games = botState.get_games(nick)
        if not games:
            return
        for chan in botState.games[nick]:
            for game in games:
                if game in botState.games[nick][chan]["games"]:
                    if game.nicks[game.player] == game.other(nick):
                        await notifyPlayers(chan)

    if botState.has_any_game(nick, channel):
        await notifyPlayers(channel)


@bot.custom_handler("join")
def onEnter(nick, channel):
    if nick == NICK:  # Ignore myself ;)
        return
    if botState.has_any_game(nick, channel):
        games = botState.get_games(nick, channel)
        msg = []
        for game in games:
            if game.nicks[game.player] == nick:
                msg.append(
                    f"<{nick}> you have a game with {game.nicks[not game.player]} and it is your turn"
                )
                botState.select_game(nick, game.other(nick), channel)
            else:
                msg.append(f"<{nick}> you have a game with {game.nicks[game.player]}")
        game = botState.get_selected_game(nick, channel)
        msg.append(f"Game with {game.other(nick)}")
        msg += game.utf8_board(nick)
        return Message(channel, message=msg)


def load_game(nick, against_nick, channel, moves):
    debug(f"Loading {nick}")
    game = botState.add_game(nick, against_nick, channel, check_invitation=False)
    for m in moves:
        game.move(m)


def load_games():
    for user in db_handler.data:
        ongoing_games = load_ongoing_games()
        nick = user[db_columns[0]]
        if nick not in ongoing_games:
            continue
        for channel in ongoing_games[nick]:
            for against_nick in ongoing_games[nick][channel]:
                moves = ongoing_games[nick][channel][against_nick]
                load_game(nick, against_nick, channel, moves)


async def on_run(bot: IrcBot):
    log("Loading ongoing games...")
    load_games()
    log("Done loading ongoing games!")
    if isinstance(bot.channels, list):
        for channel in bot.channels:
            await bot.send_message(
                Color("CHESS BOT INITIALIZED", Color.light_green, Color.black).str,
                channel,
            )
    else:
        await bot.send_message(
            Color("CHESS BOT INITIALIZED", Color.light_green, Color.black).str
        )

    while True:
        for nick in botState.invites:
            for channel in botState.invites[nick]:
                for against_nick in deepcopy(botState.invites[nick][channel]):
                    now = datetime.now().timestamp()
                    if (
                        now - botState.invites[nick][channel][against_nick]
                        > EXPIRE_INVITE_IN
                    ):
                        botState.invites[nick][channel].pop(against_nick)
                        await bot.send_message(
                            f"<{against_nick}> Your game request to {nick} has expired!",
                            channel,
                        )
        await bot.sleep(0.5)


##################################################
# RUNNING THE BOT                                #
##################################################


# Register basic commands
@bot.arg_command("board", "Displays the board", "")
def board_cmd(args, message):
    return print_board(args, message)


@bot.arg_command("b", "Alias for board", "")
def b_cmd(args, message):
    return print_board(args, message)


@bot.arg_command("undo", "Undoes the last move", "")
def undo_cmd(args, message):
    return undo(args, message)


@bot.arg_command(
    "move",
    "Moves a piece",
    f"Makes an uic movement: move [column][row][column][row]. e.g.: {PREFIX}m e2e4",
)
async def move_cmd(args, message):
    return await move(bot, args, message)


@bot.arg_command("m", "Alias for move", "")
async def m_cmd(args, message):
    return await move(bot, args, message)


@bot.arg_command(
    "colors",
    "Changes the board or pieces colors",
    f"{PREFIX}colors [pieces|board] [color1] [color2]",
)
def colors_cmd(args, message):
    return colors(args, message)


@bot.arg_command("hint", "Get posible movements", "")
def hint_cmd(args, message):
    return hint(args, message)


@bot.arg_command("score", "Player's status", f"{PREFIX}score [nick]")
def score_cmd(args, message):
    return score(args, message)


@bot.arg_command(
    "label", "Changes the top and bottom row of letters", f"{PREFIX}label [1|2]"
)
def label_cmd(args, message):
    return label(args, message)


@bot.arg_command(
    "start",
    "Starts a new game",
    f"{PREFIX}start [nick] or start {NICK} to play against the cpu.",
)
async def start_cmd(args, message):
    return await start(bot, args, message)


@bot.arg_command("accept", "Accepts a game request", f"{PREFIX}accept [nick]")
def accept_cmd(args, message):
    return accept(args, message)


@bot.arg_command(
    "client",
    "Sets specific board laytouts for some irc clients",
    f"{PREFIX}bmode hexchat",
)
def client_cmd(args, message):
    return bmode(args, message)


async def on_connect():
    for channel in CHANNELS:
        await bot.join(channel)
    await on_run(bot)


if __name__ == "__main__":
    bot.run_with_callback(on_connect)
