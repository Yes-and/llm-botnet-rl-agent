FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    vsftpd \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash admin && \
    echo 'admin:admin123' | chpasswd

RUN mkdir -p /var/run/vsftpd/empty && \
    printf 'listen=YES\nlisten_ipv6=NO\nanonymous_enable=YES\nlocal_enable=YES\nwrite_enable=NO\npam_service_name=vsftpd\nsecure_chroot_dir=/var/run/vsftpd/empty\nseccomp_sandbox=NO\n' \
    > /etc/vsftpd.conf

EXPOSE 21
CMD ["/usr/sbin/vsftpd"]
