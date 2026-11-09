FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates dnsutils netcat-openbsd iproute2 procps iputils-ping \
      nodejs npm git less \
 && rm -rf /var/lib/apt/lists/*
# Build tools baked in: the sandbox has no internet, so pip build isolation cannot fetch setuptools.
RUN pip install --no-cache-dir setuptools wheel
# uid 20001 on purpose: the lab host user is uid 1000, so an escape or stray bind mount never maps to it.
RUN groupadd -g 20001 agent && useradd -m -u 20001 -g 20001 -s /bin/bash agent
USER agent
WORKDIR /home/agent
CMD ["sleep", "infinity"]
