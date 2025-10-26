FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
RUN apt-get update -y 
RUN apt-get install -y --no-install-recommends ffmpeg 
RUN apt-get clean 
RUN rm -rf /var/lib/apt/lists/*
Fixes the "No such file or directory: 'requirements.txt'" error
COPY requirements.txt .
RUN pip3 install -U pip && pip3 install --no-cache-dir -r requirements.txt
COPY . .
CMD ["bash", "start"]
