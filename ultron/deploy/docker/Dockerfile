# Any machine with Docker (Linux, macOS, Windows). Build from the folder that holds agent.toml:
#   docker build -f ultron/deploy/docker/Dockerfile -t ultron .
#   docker run -d --name ultron --restart unless-stopped --env-file .env -v "$PWD/output:/app/output" ultron
# Secrets come in only through --env-file: keep .env, output/ and other private files out of the
# image with a .dockerignore next to agent.toml (e.g. lines: .env  output  .git).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir "/app/ultron[full]"
CMD ["ultron", "serve"]
