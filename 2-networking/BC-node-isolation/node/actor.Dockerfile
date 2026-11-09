# BC actor sandbox. Pins match the S1 node environment (S-serving/VM/environment.txt).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl jq procps ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir requests==2.34.2 httpx==0.28.1 numpy==2.3.5 aiohttp==3.14.5 openai==3.46.0
RUN useradd -m -u 1500 actor && mkdir /work && chown actor:actor /work
USER actor
WORKDIR /work
