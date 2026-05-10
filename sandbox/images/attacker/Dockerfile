FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap \
    hydra \
    netcat-openbsd \
    curl \
    openssh-client \
    sshpass \
    python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /home/agent
CMD ["bash"]