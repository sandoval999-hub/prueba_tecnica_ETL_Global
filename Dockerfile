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

# Expose Dagster UI port
EXPOSE 3000

# Set environment variables for Dagster
ENV DAGSTER_HOME=/app/dagster_home
RUN mkdir -p $DAGSTER_HOME

# Default command: Start Dagster Webserver
CMD ["dagster", "dev", "-h", "0.0.0.0", "-p", "3000", "-m", "src.orchestration"]
