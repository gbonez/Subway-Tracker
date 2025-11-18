FROM python:3.12-slim

# Install system dependencies required for Playwright browsers
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    libglib2.0-0 \
    libnspr4 \
    libnss3 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libatspi2.0-0 \
    libx11-6 \
    libgbm1 \
    libxcb1 \
    libxkbcommon0 \
    libasound2 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxcomposite1 \
    libxrandr2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN python -m playwright install chromium

# Copy application code
COPY . .

# Let Railway handle port automatically - no port specification
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0"]