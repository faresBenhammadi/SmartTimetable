FROM python:3.11-slim

# System dependencies required by psycopg2
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Hugging Face Spaces requires port 7860
EXPOSE 7860

# 1 worker + 4 threads:
#   - 1 worker keeps memory predictable
#   - 4 threads lets polling requests go through while solver runs in background thread
#   - timeout 0 disables gunicorn worker kill timer (solver can run as long as needed)
CMD ["gunicorn", "--bind", "0.0.0.0:7860", "--workers", "1", "--threads", "4", "--timeout", "0", "app:app"]
