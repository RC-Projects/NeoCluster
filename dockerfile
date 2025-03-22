FROM python:3.9-slim

# Install required packages
RUN apt-get update && apt-get install -y curl gnupg apt-transport-https && \
    curl -s https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key add - && \
    echo "deb https://apt.kubernetes.io/ kubernetes-xenial main" > /etc/apt/sources.list.d/kubernetes.list && \
    apt-get update && \
    apt-get install -y kubectl && \
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