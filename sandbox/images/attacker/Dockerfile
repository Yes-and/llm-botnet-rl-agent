FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap \
    hydra \
    netcat-openbsd \
    curl \
    openssh-client \
    sshpass \
    python3 \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /home/agent
COPY passwords.txt /usr/share/wordlists/passwords.txt
CMD ["bash"]