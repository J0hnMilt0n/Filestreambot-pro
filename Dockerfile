# Dockerfile for Koyeb deployment with YouTube support
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    unzip \
    gcc \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Deno (required for YouTube extraction)
RUN curl -fsSL https://deno.land/install.sh | sh -s v1.45.0
ENV PATH="/root/.deno/bin:${PATH}"

# Verify installations
RUN deno --version && ffmpeg -version

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directory for YouTube cookies
RUN mkdir -p /app && chmod 755 /app

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run the application
CMD ["python", "-m", "Adarsh"]