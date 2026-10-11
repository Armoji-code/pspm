# Headless dev/test image: same checks CI runs on Linux. `docker build -t pspm . && docker run --rm pspm`
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e '.[dev]'
COPY tests ./tests
ENV QT_QPA_PLATFORM=offscreen
CMD ["sh", "-c", "ruff check . && pytest -q"]
