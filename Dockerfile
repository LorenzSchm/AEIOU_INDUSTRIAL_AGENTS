FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY vendor/arena-kit vendor/arena-kit
COPY src src
RUN uv sync --frozen --no-dev
COPY data/package data/package
COPY reports reports
COPY SYSTEM_PROMPT.md ./
EXPOSE 8765
CMD ["uv", "run", "--no-sync", "uvicorn", "aeiou.service:app", "--host", "0.0.0.0", "--port", "8765"]
