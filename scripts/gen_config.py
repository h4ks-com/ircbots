# Description: Generate a global config file for all bots
import json
from pathlib import Path
from sys import argv

from dotenv import dotenv_values

global_config = {}

path = "*/.env.example"
if len(argv) > 1 and argv[1] == "real":
    path = "*/.env"

bots = Path("bots").glob(path)
for bot in bots:
    env = dotenv_values(bot, interpolate=True)
    global_config[f"bots/{bot.parent.name}"] = dict(env)

print(json.dumps(global_config, indent=2))
