FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN echo "deb http://archive.ubuntu.com/ubuntu jammy universe" >> /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    netbase \
    openbsd-inetd \
    telnetd \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash admin && \
    echo 'admin:Xk9mP2vL8nQ5rT' | chpasswd

EXPOSE 23
CMD ["/usr/sbin/inetd", "-d"]
