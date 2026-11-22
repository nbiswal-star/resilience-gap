FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY sql ./sql
COPY seeds ./seeds
# Editable install so the package resolves config/, sql/ and seeds/ from /app.
RUN pip install --no-cache-dir -e ".[s3]"
CMD ["resgap", "--help"]
