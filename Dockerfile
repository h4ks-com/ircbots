FROM python:3.12.5-slim-bullseye
RUN apt-get update && apt-get install -y curl nodejs npm jq ffmpeg && \
    npm install -g pm2 && pm2 install pm2-logrotate && \
    pm2 set pm2-logrotate:max_size 10M && pm2 set pm2-logrotate:compress true && \
    npm install pm2-gui -g

COPY . /ircbots/
WORKDIR /ircbots
RUN bash scripts/build_envs.sh

ENTRYPOINT ["bash", "scripts/launch_bots.sh"]
