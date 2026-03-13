FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y ffmpeg nodejs npm && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY . .

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Start server using Railway PORT
CMD ["sh", "-c", "gunicorn main_download_code:app --bind 0.0.0.0:$PORT"]
