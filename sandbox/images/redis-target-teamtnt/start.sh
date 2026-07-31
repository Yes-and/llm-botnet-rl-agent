#!/bin/bash
# Both processes need to run in the same container: sshd is the thing the planted
# key actually grants access to, redis-server is the thing being exploited to plant it.
/usr/sbin/sshd
exec redis-server --protected-mode no
