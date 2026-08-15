# Stage 1: fetch a real, minimal image at BUILD time. The host running `docker build`
# has normal internet access here — this never touches the sandbox's own runtime network
# isolation (`internal: true`), same principle as the Mirai credential list being baked
# into the attacker image at build time rather than fetched at runtime.
#
# Without this, the target is impossible to fully compromise: confirmed empirically
# 2026-07-31 that the nested daemon has no runtime internet access (DNS resolution
# fails), and the attacker image has no static-linked binary to fabricate a working
# image from scratch — so a real, working shell can never be obtained, only a
# container that starts and immediately exits. skopeo pulls+saves an image without
# needing any running Docker daemon (which a build stage can't easily provide anyway).
FROM quay.io/skopeo/stable:latest AS image-fetch
RUN skopeo copy docker://docker.io/library/alpine:latest docker-archive:/alpine.tar:alpine:latest

FROM docker:dind

COPY --from=image-fetch /alpine.tar /alpine.tar
COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
