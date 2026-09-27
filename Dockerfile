FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY sdlc_lens ./sdlc_lens
RUN pip install --no-cache-dir '.[postgres]'

EXPOSE 8080
ENTRYPOINT ["sdlc-lens"]
