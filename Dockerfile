FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 korsik && useradd --uid 10001 --gid korsik --create-home korsik
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY examples ./examples
COPY scripts ./scripts
COPY tests ./tests
RUN pip install --no-cache-dir --no-deps . && mkdir /app/data && chown -R korsik:korsik /app/data
USER korsik
EXPOSE 8000
CMD ["python", "-m", "korsik.cli", "serve", "--host", "0.0.0.0"]
