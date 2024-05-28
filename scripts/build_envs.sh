#!/usr/bin/env bash
set -ex -o pipefail

is_dev=false
if [ "$1" == "dev" ]; then
	# Install dev dependencies
	is_dev=true
fi

global_python="$(which python3)"

# Loop throught directories in bots/ and build a separate environment for each using requirements.txt
for bot in bots/*; do
	if [ -d "$bot" ]; then
		pushd "$bot"
		bot_venv_dir="$(pwd)/.venv"
		$global_python -m venv "$bot_venv_dir"
		source "$bot_venv_dir/bin/activate"

		if [ "$is_dev" = true ] && [ -f requirements-dev.txt ]; then
			pip install --no-cache-dir install -r requirements-dev.txt
		fi
		pip install --no-cache-dir install -r requirements.txt

		deactivate
		popd
	fi
done
