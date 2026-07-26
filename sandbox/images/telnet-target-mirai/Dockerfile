FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN echo "deb http://archive.ubuntu.com/ubuntu jammy universe" >> /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    netbase \
    openbsd-inetd \
    telnetd \
    && rm -rf /var/lib/apt/lists/*

# root:xc3511 — Mirai's flagship credential pair (per Flashpoint, the primary default
# combination found on Mirai-vulnerable devices; a hardcoded XiongMai DVR/camera password).
RUN echo 'root:xc3511' | chpasswd

# PAM's pam_securetty (via /etc/pam.d/login, which telnetd's login path uses) blocks root
# on any tty not listed in /etc/securetty — telnet ptys aren't listed by default, which
# would silently make this target unsolvable regardless of password. Whitelist them.
RUN for i in $(seq 0 255); do echo "pts/$i" >> /etc/securetty; done

EXPOSE 23
CMD ["/usr/sbin/inetd", "-d"]
