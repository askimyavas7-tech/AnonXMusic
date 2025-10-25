FROM python:3.10-slim-buster

WORKDIR /app

RUN apt-get update && \
    apt-get install -y ffmpeg git curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip3 install --no-cache-dir -U pip setuptools wheel
RUN pip3 install --no-cache-dir -r requirements.txt

CMD bash start
