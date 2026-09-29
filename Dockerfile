FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home finance
COPY alembic.ini ./
COPY migrations ./migrations
RUN chmod -R a+rX /app && mkdir -p data/private/models && chown -R finance:finance data
USER finance
EXPOSE 8000
CMD ["uvicorn", "gzp_finance.web:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
