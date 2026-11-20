# One image for both services; docker-compose.yml chooses the command.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Run as an unprivileged user.
RUN useradd --create-home appuser
USER appuser

CMD ["python", "-m", "crashtest.sink"]
