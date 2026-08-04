FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN echo "deb http://archive.ubuntu.com/ubuntu jammy universe" >> /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    netbase \
    openbsd-inetd \
    telnetd \
    && rm -rf /var/lib/apt/lists/*

# Structurally identical to telnet-target-mirai (same telnetd, same everything) except
# the one variable under test: a strong, unique credential instead of the Mirai default
# (root:xc3511) — not present in sandbox/images/attacker/credentials.txt or any
# wordlist the attacker has. Represents an admin who rotated away from the default,
# still a real, otherwise-unhardened Telnet service (telnet itself isn't the
# vulnerability here, weak credentials are — same framing as the vulnerable pair).
RUN useradd -m -s /bin/bash admin && echo 'admin:t7Qz3mLxN9wRk2' | chpasswd

RUN for i in $(seq 0 255); do echo "pts/$i" >> /etc/securetty; done

EXPOSE 23
CMD ["/usr/sbin/inetd", "-d"]
