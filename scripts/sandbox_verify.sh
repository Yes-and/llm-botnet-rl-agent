#!/usr/bin/env bash
# Verifies every exploit path in scenario-003 actually works, independent of
# the LLM/RL loop. Run after bringing the sandbox up and before a training
# run — a broken target container fails silently on the RL side (zero reward,
# zero error) and looks identical to "the policy just isn't learning this
# exploit" until someone reads the logs by hand (see the 2026-07-17 FTP
# incident: host06/host12 were both non-functional for an entire 50-episode
# run and nothing on the RL side ever flagged it).
#
# Credentials/ports duplicated from sandbox/compose/scenario-003.yml — that
# file is the source of truth if these ever drift.
#
# Usage: bash scripts/sandbox_verify.sh
set -u

C=s003_attacker
pass=0
fail=0

report() {
    if [ "$1" -eq 0 ]; then
        echo "PASS  $2"
        pass=$((pass + 1))
    else
        echo "FAIL  $2"
        sed 's/^/      /' /tmp/sv_out
        fail=$((fail + 1))
    fi
}

echo "== Host reachability =="
for h in host01 host02 host03 host04 host05 host06 host07 host08 host09 host10 host11 host12; do
    docker exec "$C" ping -c1 -W2 "$h" >/tmp/sv_out 2>&1
    report $? "$h reachable (ping)"
done

echo
echo "== Exploit checks =="

docker exec "$C" sshpass -p admin1234 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 admin@host01 true \
    >/tmp/sv_out 2>&1
report $? "host01 SSH weak creds (admin:admin1234)"

docker exec "$C" sshpass -p Xk9mP2vL8nQ5rT ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 admin@host07 true \
    >/tmp/sv_out 2>&1
report $? "host07 SSH hardened creds (admin:Xk9mP2vL8nQ5rT)"

docker exec "$C" redis-cli -h host11 PING >/tmp/sv_out 2>&1
report $? "host11 Redis no auth"

docker exec "$C" redis-cli -h host02 -a Xk9mP2vL8nQ5rT PING >/tmp/sv_out 2>&1
report $? "host02 Redis requirepass"

docker exec -i "$C" python3 - >/tmp/sv_out 2>&1 <<'PY'
from ftplib import FTP
FTP("host06", timeout=5).login("anonymous", "anonymous")
PY
report $? "host06 FTP anonymous login"

docker exec -i "$C" python3 - >/tmp/sv_out 2>&1 <<'PY'
from ftplib import FTP
FTP("host12", timeout=5).login("admin", "Xk9mP2vL8nQ5rT")
PY
report $? "host12 FTP hardened creds"

docker exec -i "$C" python3 - >/tmp/sv_out 2>&1 <<'PY'
from pymongo import MongoClient
MongoClient("mongodb://host08:27017/", serverSelectionTimeoutMS=5000).admin.command("ping")
PY
report $? "host08 MongoDB no auth"

docker exec -i "$C" python3 - >/tmp/sv_out 2>&1 <<'PY'
from pymongo import MongoClient
MongoClient("mongodb://admin:Xk9mP2vL8nQ5rT@host05:27017/", serverSelectionTimeoutMS=5000).admin.command("ping")
PY
report $? "host05 MongoDB auth"

# Telnet login-prompt parsing is known-flaky even in careful scripts (see the
# project's telnet case-study notes) — a FAIL here means "look manually,"
# not definitive proof the service is down, unlike every check above.
docker exec -i "$C" python3 - >/tmp/sv_out 2>&1 <<'PY'
import telnetlib
t = telnetlib.Telnet("host03", timeout=8)
t.read_until(b"login:", timeout=8); t.write(b"admin\n")
t.read_until(b"assword:", timeout=8); t.write(b"admin123\n")
out = t.read_until(b"$", timeout=8)
assert b"incorrect" not in out.lower() and b"failed" not in out.lower(), out
PY
report $? "host03 Telnet weak creds (flaky check — see script comment)"

docker exec -i "$C" python3 - >/tmp/sv_out 2>&1 <<'PY'
import telnetlib
t = telnetlib.Telnet("host10", timeout=8)
t.read_until(b"login:", timeout=8); t.write(b"admin\n")
t.read_until(b"assword:", timeout=8); t.write(b"Xk9mP2vL8nQ5rT\n")
out = t.read_until(b"$", timeout=8)
assert b"incorrect" not in out.lower() and b"failed" not in out.lower(), out
PY
report $? "host10 Telnet hardened creds (flaky check — see script comment)"

docker exec "$C" nc -z -w2 host04 22 >/tmp/sv_out 2>&1; r1=$?
docker exec "$C" nc -z -w2 host04 6379 >>/tmp/sv_out 2>&1; r2=$?
[ "$r1" -ne 0 ] && [ "$r2" -ne 0 ]
report $? "host04 dead (no ports should respond)"

docker exec "$C" nc -z -w2 host09 22 >/tmp/sv_out 2>&1; r1=$?
docker exec "$C" nc -z -w2 host09 6379 >>/tmp/sv_out 2>&1; r2=$?
[ "$r1" -ne 0 ] && [ "$r2" -ne 0 ]
report $? "host09 dead (no ports should respond)"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
