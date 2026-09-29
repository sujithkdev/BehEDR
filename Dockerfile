# Dockerfile for BehEDR Server
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (for caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install optional dependencies (ML, threat intel)
RUN pip install --no-cache-dir \
    tensorflow \
    torch \
    dgl \
    pyinstaller \
    pywin32 || true
    # || true allows build to continue if optional deps fail

# Copy application code
COPY . .

# Create directories
RUN mkdir -p /app/data /app/logs /app/models /app/rules

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

# Run server
CMD ["python", "run.py"]
