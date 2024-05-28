#!/usr/bin/env bash
# set -ex -o pipefail
set -e -o pipefail

if [ -n "$CD_ENV_VARS" ]; then
	echo "Setting environment variables from base64 encoded string"
	echo "$CD_ENV_VARS" | base64 -d | tr -d '\n' > config.json
fi

if [ ! -f "config.json" ]; then
	echo "config.json file not found"
	exit 1
fi

# Launch all bots in parallel
for bot in bots/*; do
	env_contents=$(jq -r '."'"$bot"'"' <config.json)
	python scripts/json2env.py "$env_contents" >"$bot/.env"

	if [ -d "$bot" ]; then
		pushd "$bot"
		VENV_DIR="$(pwd)/.venv"
		source "$VENV_DIR/bin/activate"
		interpreter=$(which python3)
		pm2 start --name "$(basename "$bot")" --interpreter "$interpreter" main.py
		deactivate
		popd
	fi
done

pm2 start "pm2-gui start '$(pwd)/configs/pm2-gui.ini'"
pm2 logs
