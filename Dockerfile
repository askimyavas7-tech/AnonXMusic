FROM python:3.10-slim-bullseye

# Dependencies & FFmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg python3-venv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy project
WORKDIR /app
COPY . .

# Python deps
RUN pip3 install --upgrade pip
RUN pip3 install -r requirements.txt

# Start Permission
RUN chmod +x start.sh

# Launch bot
CMD ["bash", "start.sh"]
