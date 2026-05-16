FROM python:3.11-slim

# Metadata
LABEL maintainer="Atlas RA Data Engineering"
LABEL description="Seismic ETL Pipeline"

# System dependencies for PyMySQL / matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create runtime directories
RUN mkdir -p logs output/charts

# Default command (overridden by docker-compose)
CMD ["python", "main.py", "--mode", "daily"]
