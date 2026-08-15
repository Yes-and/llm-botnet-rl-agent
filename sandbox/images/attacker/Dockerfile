FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap \
    hydra \
    netcat-openbsd \
    curl \
    openssh-client \
    sshpass \
    python3 \
    python3-pymongo \
    iproute2 \
    iputils-ping \
    telnet \
    ftp \
    redis-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /home/agent
# Real Mirai botnet default-credential pairs (user:pass), for use with hydra's -C flag.
COPY credentials.txt /usr/share/wordlists/credentials.txt
CMD ["bash"]