FROM python:3.12-slim

WORKDIR /app

# Install only the runtime dependencies declared in pyproject (no dev extras
# such as pytest are installed in the image).
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app

# Run as a non-root user rather than root.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
