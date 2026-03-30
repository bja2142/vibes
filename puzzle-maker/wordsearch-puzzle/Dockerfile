FROM python:3.11-slim

# Install system dependencies and fonts
RUN apt-get update && apt-get install -y \
    libfreetype6-dev \
    libjpeg-dev \
    zlib1g-dev \
    fonts-dejavu-core \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ensure Python output is sent straight to terminal without buffering
ENV PYTHONUNBUFFERED=1

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create storage and data directories
RUN mkdir -p /app/storage /app/data

# Expose the web port
EXPOSE 8000

# Set the entrypoint to the dispatcher script
RUN chmod +x entrypoint.sh
ENTRYPOINT ["./entrypoint.sh"]
