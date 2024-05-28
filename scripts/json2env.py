import json
from sys import argv

input = argv[1]
obj = json.loads(input)
for key in obj or {}:
    print(f"{key}={obj[key]}")
