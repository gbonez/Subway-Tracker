FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

# Copy your code
COPY . .

# Default command - run either the web app or the queue worker based on APP_ROLE.
CMD ["sh", "-c", "if [ \"${APP_ROLE:-web}\" = \"worker\" ]; then python worker.py; else uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}; fi"]