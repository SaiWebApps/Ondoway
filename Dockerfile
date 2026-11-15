FROM python:3.13-slim

WORKDIR /app

# Copy pre-exported requirements (pinned versions + hashes)
COPY requirements.txt ./

# Install production dependencies from public PyPI
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code. scripts/ is a runtime dependency of src/onboard
# (beat_builder.slugify at import time, validate_beats + `python -m
# scripts.deploy` at runtime) — test_docker_image_contents.py guards this.
COPY src/ src/
COPY scripts/ scripts/
COPY web/site/public/ web/site/public/

# Expose port
EXPOSE 8000

# Run with uvicorn
CMD ["sh", "-c", "exec uvicorn src.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
