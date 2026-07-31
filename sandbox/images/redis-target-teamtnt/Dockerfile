FROM redis:6.2

# Real TeamTNT/Kinsing campaigns exploited hosts where Redis ran as root AND an SSH
# server was reachable on the same box — the official image runs redis-server as an
# unprivileged `redis` user (its own security hardening) and has no SSH server at all,
# so the classic "plant a key in /root/.ssh/authorized_keys" technique has nowhere real
# to land (confirmed 2026-07-31: CONFIG SET dir /root/.ssh -> "Permission denied", and
# no sshd to log into even if it had worked). Dedicated fork, not an edit to a shared
# image — same blast-radius rationale as scenario-004's telnet-target-mirai.
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd /root/.ssh \
    && chmod 700 /root/.ssh \
    && ssh-keygen -A \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config

# Reset to root — the base image switches to `USER redis` for its normal hardened
# default; the real campaigns' vulnerable shape needs Redis writing files as root.
USER root

COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
