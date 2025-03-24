FROM --platform=linux/arm64 python:3.9-slim

# Install kubectl for ARM architecture
RUN apt-get update && apt-get install -y curl && \
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/arm64/kubectl" && \
    chmod +x kubectl && \
    mv kubectl /usr/local/bin/ && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY k8s_metrics_api.py .
COPY static/ ./static/

# Expose the port
EXPOSE 8080

# Run the application
CMD ["python", "k8s_metrics_api.py"]