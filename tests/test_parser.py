"""
Table-driven parser tests. Each case is derived from a real run log.
To add a new case: append a row to the relevant parametrize list.
"""

import pytest
from rl.parser import parse_step


# ── nmap ─────────────────────────────────────────────────────────────────────

NMAP_CASES = [
    pytest.param(
        # step 2: host discovery, timed out (exit 124) — partial output still valid.
        # .1 has no reverse-DNS hostname — Docker gateway noise, not a real container.
        "nmap -sn 172.18.0.0/16 -oG -",
        124,
        "Host: 172.18.0.1 ()\tStatus: Up\n"
        "Host: 172.18.0.2 (s002_mongodb)\tStatus: Up\n"
        "Host: 172.18.0.3 (s002_ssh)\tStatus: Up\n",
        {"172.18.0.2": {"is_alive": True},
         "172.18.0.3": {"is_alive": True}},
        {"172.18.0.1"},
        id="host-discovery-timeout",
    ),
    pytest.param(
        # step 3: service scan with known ports
        "nmap -sV -F 172.18.0.1-6 -oG -",
        0,
        "Host: 172.18.0.3 (s002_ssh)\tStatus: Up\n"
        "Host: 172.18.0.3 (s002_ssh)\tPorts: 22/open/tcp//ssh//OpenSSH 8.9p1/\tIgnored State: closed (99)\n"
        "Host: 172.18.0.4 (s002_ftp)\tStatus: Up\n"
        "Host: 172.18.0.4 (s002_ftp)\tPorts: 21/open/tcp//ftp//vsftpd 3.0.5/\tIgnored State: closed (99)\n"
        "Host: 172.18.0.6 (s002_telnet)\tStatus: Up\n"
        "Host: 172.18.0.6 (s002_telnet)\tPorts: 23/open/tcp//telnet//Linux telnetd/\tIgnored State: closed (99)\n",
        {"172.18.0.3": {"is_alive": True, "port_22_open": True, "service_ssh": True},
         "172.18.0.4": {"is_alive": True, "port_21_open": True, "service_ftp": True},
         "172.18.0.6": {"is_alive": True, "port_23_open": True, "service_telnet": True}},
        set(),
        id="port-scan-known-services",
    ),
    pytest.param(
        # step 5: full port scan finds redis and mongo
        "nmap -p- 172.18.0.2 172.18.0.5 -oG -",
        0,
        "Host: 172.18.0.2 (s002_mongodb)\tStatus: Up\n"
        "Host: 172.18.0.2 (s002_mongodb)\tPorts: 27017/open/tcp//mongod///\tIgnored State: closed (65534)\n"
        "Host: 172.18.0.5 (s002_redis)\tStatus: Up\n"
        "Host: 172.18.0.5 (s002_redis)\tPorts: 6379/open/tcp//redis///\tIgnored State: closed (65534)\n",
        {"172.18.0.2": {"is_alive": True, "port_27017_open": True},
         "172.18.0.5": {"is_alive": True, "port_6379_open": True}},
        set(),
        id="full-scan-redis-mongo",
    ),
    pytest.param(
        # a real host with no open ports is still marked alive
        "nmap -sV -F 172.18.0.7 -oG -",
        0,
        "Host: 172.18.0.7 (s002_hardened)\tStatus: Up\n"
        "Host: 172.18.0.7 (s002_hardened)\tPorts: \tIgnored State: closed (100)\n",
        {"172.18.0.7": {"is_alive": True}},
        set(),
        id="host-alive-no-ports",
    ),
    pytest.param(
        # no reverse-DNS hostname at all (empty parens, no ports line either) — pure
        # Docker gateway noise, must not be registered as a host under any feature.
        "nmap -sV -F 172.18.0.1 -oG -",
        0,
        "Host: 172.18.0.1 ()\tStatus: Up\n"
        "Host: 172.18.0.1 ()\tPorts: \tIgnored State: closed (100)\n",
        {},
        {"172.18.0.1"},
        id="gateway-excluded-no-hostname",
    ),
    pytest.param(
        # human-readable format, exit 0 — real output from run.log step 3.
        # .1 has no hostname prefix — Docker gateway, not a real container.
        "nmap -sn 172.18.0.0/24",
        0,
        "Starting Nmap 7.93 ( https://nmap.org ) at 2026-06-05 11:34 UTC\n"
        "Nmap scan report for 172.18.0.1\nHost is up (0.000050s latency).\n"
        "MAC Address: CE:98:27:FD:93:5A (Unknown)\n"
        "Nmap scan report for s002_mongodb.scenario-002_s002_net (172.18.0.2)\n"
        "Host is up (0.000026s latency).\n"
        "Nmap scan report for s002_ssh.scenario-002_s002_net (172.18.0.3)\n"
        "Host is up (0.000023s latency).\n"
        "Nmap done: 256 IP addresses (3 hosts up) scanned in 1.97 seconds\n",
        {"172.18.0.2": {"is_alive": True},
         "172.18.0.3": {"is_alive": True}},
        {"172.18.0.1"},
        id="host-discovery-human-readable",
    ),
    pytest.param(
        # human-readable format, timed out (exit 124) — partial output still parsed
        "nmap -sn 172.18.0.0/16",
        124,
        "Starting Nmap 7.93 ( https://nmap.org ) at 2026-06-05 11:33 UTC\n"
        "Nmap scan report for 172.18.0.1\nHost is up (0.0000090s latency).\n"
        "MAC Address: CE:98:27:FD:93:5A (Unknown)\n"
        "Nmap scan report for s002_mongodb.scenario-002_s002_net (172.18.0.2)\n"
        "Host is up (0.000036s latency).\n"
        "[TIMEOUT] Command exceeded 60s and was killed.\n",
        {"172.18.0.2": {"is_alive": True}},
        {"172.18.0.1"},
        id="host-discovery-human-readable-timeout",
    ),
]


@pytest.mark.parametrize("command,exit_code,output,expected_updates,expected_absent", NMAP_CASES)
def test_nmap(command, exit_code, output, expected_updates, expected_absent):
    result = parse_step(command, output, exit_code)
    updates = dict(result.state_updates)
    for ip, feats in expected_updates.items():
        assert ip in updates, f"{ip} not in updates"
        for feat, val in feats.items():
            assert updates[ip].get(feat) == val, f"{ip}.{feat} expected {val}"
    for ip in expected_absent:
        assert ip not in updates, f"{ip} should be excluded (no reverse-DNS hostname — Docker infra noise)"
    assert result.exploit is None


# ── hydra ─────────────────────────────────────────────────────────────────────

HYDRA_CASES = [
    pytest.param(
        # no credentials found (step 25)
        "hydra -L /tmp/users.txt -P /tmp/passes.txt -t 4 telnet://172.18.0.6:23",
        0,
        "1 of 1 target completed, 0 valid password found\n",
        {},
        id="hydra-no-creds-found",
    ),
    pytest.param(
        # timed out (step 26)
        "hydra -L /tmp/users.txt -P /tmp/passes.txt -t 4 ssh://172.18.0.3:22",
        124,
        "[STATUS] 75.00 tries/min, 75 tries in 00:01h, 1 to do in 00:01h, 2 active\n",
        {},
        id="hydra-timeout",
    ),
    pytest.param(
        # ssh credential found (success pattern not seen in this run; from scenario-001 docs)
        "hydra -l admin -P /usr/share/wordlists/passwords.txt ssh://172.18.0.3",
        0,
        "[22][ssh] host: 172.18.0.3   login: admin   password: admin123\n"
        "1 of 1 target successfully completed, 1 valid password found\n",
        {"172.18.0.3": {"creds_found": True, "service_ssh": True}},
        id="hydra-ssh-creds-found",
    ),
    pytest.param(
        # ftp credential found
        "hydra -l admin -P /usr/share/wordlists/passwords.txt ftp://172.18.0.4",
        0,
        "[21][ftp] host: 172.18.0.4   login: admin   password: admin123\n",
        {"172.18.0.4": {"creds_found": True, "service_ftp": True}},
        id="hydra-ftp-creds-found",
    ),
    pytest.param(
        # telnet credential found
        "hydra -l admin -P /usr/share/wordlists/passwords.txt telnet://172.18.0.6",
        0,
        "[23][telnet] host: 172.18.0.6   login: admin   password: admin123\n",
        {"172.18.0.6": {"creds_found": True, "service_telnet": True}},
        id="hydra-telnet-creds-found",
    ),
]


@pytest.mark.parametrize("command,exit_code,output,expected_updates", HYDRA_CASES)
def test_hydra(command, exit_code, output, expected_updates):
    result = parse_step(command, output, exit_code)
    updates = dict(result.state_updates)
    for ip, feats in expected_updates.items():
        for feat, val in feats.items():
            assert updates.get(ip, {}).get(feat) == val
    assert result.exploit is None


# ── redis-cli ─────────────────────────────────────────────────────────────────

REDIS_CASES = [
    pytest.param(
        # INFO with pipe (step 7)
        "redis-cli -h 172.18.0.5 INFO | head -20",
        0,
        "# Server\nredis_version:8.8.0\ntcp_port:6379\n",
        "172.18.0.5", True, "redis_no_auth",
        id="redis-info-success",
    ),
    pytest.param(
        # bare INFO — exact pattern observed in real training runs
        "redis-cli -h 172.21.0.2 INFO",
        0,
        "# Server\nredis_version:7.2.1\nredis_git_sha1:00000000\ntcp_port:6379\n",
        "172.21.0.2", True, "redis_no_auth",
        id="redis-info-bare-success",
    ),
    pytest.param(
        # KEYS '*' returns empty — no redis_version: in output, not an exploit indicator
        "redis-cli -h 172.18.0.5 KEYS '*'",
        0,
        "",
        "172.18.0.5", False, None,
        id="redis-keys-no-exploit",
    ),
    pytest.param(
        # CONFIG GET returns data — no redis_version: in output, not an exploit indicator
        "redis-cli -h 172.18.0.5 CONFIG GET dir",
        0,
        "dir\n/data\n",
        "172.18.0.5", False, None,
        id="redis-config-get-no-exploit",
    ),
    pytest.param(
        # PING returns PONG — connectivity only, not auth bypass
        "redis-cli -h 172.18.0.5 PING",
        0,
        "PONG\n",
        "172.18.0.5", False, None,
        id="redis-ping-no-exploit",
    ),
    pytest.param(
        # CONFIG SET fails with ERR (step 28)
        "redis-cli -h 172.18.0.5 CONFIG SET dir /etc/cron.d",
        0,
        "ERR CONFIG SET failed (possibly related to argument 'dir') - can't set protected config\n",
        "172.18.0.5", False, None,
        id="redis-err-response-not-success",
    ),
    pytest.param(
        # non-zero exit code
        "redis-cli -h 172.18.0.5 PING",
        1,
        "Could not connect to Redis\n",
        "172.18.0.5", False, None,
        id="redis-nonzero-exit",
    ),
]


@pytest.mark.parametrize("command,exit_code,output,ip,expect_access,expect_vuln", REDIS_CASES)
def test_redis_cli(command, exit_code, output, ip, expect_access, expect_vuln):
    result = parse_step(command, output, exit_code)
    updates = dict(result.state_updates)
    if expect_access:
        assert updates.get(ip, {}).get("shell_access") is True
        assert result.exploit is not None
        assert result.exploit.host == ip
        assert result.exploit.vulnerability == expect_vuln
    else:
        assert not updates.get(ip, {}).get("shell_access")
        assert result.exploit is None


# ── pymongo ───────────────────────────────────────────────────────────────────

MONGO_CASES = [
    pytest.param(
        # list_database_names succeeds (step 13)
        "python3 -c \"\nfrom pymongo import MongoClient\nclient = MongoClient('172.18.0.2', 27017, serverSelectionTimeoutMS=3000)\nprint(client.list_database_names())\n\"",
        0,
        "['admin', 'config', 'local']\n",
        "172.18.0.2", True, "mongodb_no_auth",
        id="mongo-list-databases-success",
    ),
    pytest.param(
        # collection enumeration (step 14)
        "python3 -c \"\nfrom pymongo import MongoClient\nclient = MongoClient('172.18.0.2', 27017)\ndb = client.admin\nprint(db.list_collection_names())\n\"",
        0,
        "Collections: ['system.version']\n",
        "172.18.0.2", True, "mongodb_no_auth",
        id="mongo-list-collections-success",
    ),
    pytest.param(
        # URI format — exact pattern observed in real training runs
        "python3 -c \"from pymongo import MongoClient; client = MongoClient('mongodb://172.21.0.5:27017/'); print(client.list_database_names())\"",
        0,
        "['admin', 'config', 'local']\n",
        "172.21.0.5", True, "mongodb_no_auth",
        id="mongo-uri-format-success",
    ),
    pytest.param(
        # connection timeout
        "python3 -c \"\nfrom pymongo import MongoClient\nclient = MongoClient('172.18.0.99', 27017, serverSelectionTimeoutMS=3000)\nprint(client.list_database_names())\n\"",
        1,
        "Traceback (most recent call last):\n  ...\npymongo.errors.ServerSelectionTimeoutError: ...\n",
        "172.18.0.99", False, None,
        id="mongo-timeout-failure",
    ),
    pytest.param(
        # server_info()/buildInfo succeeds pre-auth on a hardened deployment too —
        # real false positive seen in s003-train-minimax-m27-masked-baseline-002
        # (host05, auth required) after list_database_names() correctly failed first.
        "python3 -c \"from pymongo import MongoClient; client = MongoClient('172.21.0.11', 27017, serverSelectionTimeoutMS=5000); print(client.server_info())\"",
        0,
        "{'version': '4.4.30', 'gitVersion': '4d7fa6a6260d25c5caa971dce10561690cb79dea', ...}\n",
        "172.21.0.11", False, None,
        id="mongo-server-info-preauth-not-a-breach",
    ),
]


@pytest.mark.parametrize("command,exit_code,output,ip,expect_access,expect_vuln", MONGO_CASES)
def test_mongo(command, exit_code, output, ip, expect_access, expect_vuln):
    result = parse_step(command, output, exit_code)
    updates = dict(result.state_updates)
    if expect_access:
        assert updates.get(ip, {}).get("shell_access") is True
        assert result.exploit is not None
        assert result.exploit.vulnerability == expect_vuln
    else:
        assert not updates.get(ip, {}).get("shell_access")
        assert result.exploit is None


# ── FTP (python3 ftplib) ──────────────────────────────────────────────────────

FTP_PYLIB_CASES = [
    pytest.param(
        # retrlines LIST (step 16)
        "python3 -c \"\nfrom ftplib import FTP\nftp = FTP('172.18.0.4', timeout=5)\nftp.login('anonymous', 'anonymous')\nprint(ftp.retrlines('LIST'))\n\"",
        0,
        "226 Directory send OK.\n",
        "172.18.0.4", True, "ftp_anonymous_login",
        id="ftp-pylib-relist-success",
    ),
    pytest.param(
        # empty directory listing (step 17)
        "python3 -c \"\nfrom ftplib import FTP\nftp = FTP('172.18.0.4', timeout=5)\nftp.login('anonymous', 'anonymous')\nfiles = []\nftp.retrlines('LIST', files.append)\n\"",
        0,
        "",
        "172.18.0.4", True, "ftp_anonymous_login",
        id="ftp-pylib-empty-dir-success",
    ),
    pytest.param(
        # traceback on mlsd (step 19) — connection succeeded but command failed; exit 1
        "python3 -c \"\nfrom ftplib import FTP\nftp = FTP('172.18.0.4', timeout=5)\nftp.login('anonymous', 'anonymous')\nfor f in ftp.mlsd():\n    print(f)\n\"",
        1,
        "PWD: /\nFiles:\nTraceback (most recent call last):\n  ...\nftplib.error_perm: 500 Unknown command.\n",
        "172.18.0.4", False, None,
        id="ftp-pylib-traceback-failure",
    ),
    pytest.param(
        # PWD success (step 20)
        "python3 -c \"\nfrom ftplib import FTP\nftp = FTP('172.18.0.4', timeout=5)\nftp.login('anonymous', 'anonymous')\nprint('PWD:', ftp.pwd())\n\"",
        0,
        "PWD: /\n",
        "172.18.0.4", True, "ftp_anonymous_login",
        id="ftp-pylib-pwd-success",
    ),
]


@pytest.mark.parametrize("command,exit_code,output,ip,expect_access,expect_vuln", FTP_PYLIB_CASES)
def test_ftp_pylib(command, exit_code, output, ip, expect_access, expect_vuln):
    result = parse_step(command, output, exit_code)
    updates = dict(result.state_updates)
    if expect_access:
        assert updates.get(ip, {}).get("shell_access") is True
        assert result.exploit is not None
        assert result.exploit.vulnerability == expect_vuln
    else:
        assert not updates.get(ip, {}).get("shell_access")
        assert result.exploit is None


# ── FTP (binary) ──────────────────────────────────────────────────────────────

def test_ftp_bin_success():
    command = "ftp -p -n 172.18.0.4 <<'EOF'\nuser anonymous anonymous\nls\nbye\nEOF"
    output = "Connected to 172.18.0.4.\n220 (vsFTPd 3.0.5)\n230 Login successful.\nRemote system type is UNIX.\n"
    result = parse_step(command, output, 0)
    updates = dict(result.state_updates)
    assert updates.get("172.18.0.4", {}).get("shell_access") is True
    assert result.exploit is not None
    assert result.exploit.vulnerability == "ftp_anonymous_login"


def test_ftp_bin_false_positive():
    # ftp binary exits 0 on a non-FTP host (connection refused) — must not emit exploit
    command = "ftp -p -n 172.18.0.3 <<'EOF'\nuser anonymous anonymous\nbye\nEOF"
    output = "ftp: connect: Connection refused\n"
    result = parse_step(command, output, 0)
    assert result.exploit is None


def test_ftp_bin_failure():
    command = "ftp -p -n 172.18.0.4 <<'EOF'\nuser anonymous anonymous\nbye\nEOF"
    result = parse_step(command, "Login failed.\n", 1)
    assert result.exploit is None


# ── SSH (sshpass) ─────────────────────────────────────────────────────────────

def test_ssh_permission_denied():
    # step 31: sshpass with wrong password
    command = "sshpass -p 'root' ssh -o StrictHostKeyChecking=no root@172.18.0.3 'id' 2>&1 | head -5"
    output = "Warning: Permanently added '172.18.0.3' (ED25519) to the list of known hosts.\nPermission denied, please try again.\n"
    result = parse_step(command, output, 0)
    assert result.exploit is None
    assert not dict(result.state_updates).get("172.18.0.3", {}).get("shell_access")


def test_ssh_shell_access_success():
    # success pattern from scenario-001 verified run
    command = "sshpass -p admin123 ssh admin@172.18.0.3 'id'"
    output = "uid=1000(admin) gid=1000(admin) groups=1000(admin)\n"
    result = parse_step(command, output, 0)
    updates = dict(result.state_updates)
    assert updates.get("172.18.0.3", {}).get("shell_access") is True
    assert updates.get("172.18.0.3", {}).get("creds_found") is True
    assert result.exploit is not None
    assert result.exploit.host == "172.18.0.3"
    assert result.exploit.vulnerability == "ssh_weak_credentials"


# ── telnetlib ─────────────────────────────────────────────────────────────────

def test_telnetlib_login_prompt_detected():
    # step 23: python3 -c "import telnetlib; t = Telnet('172.18.0.6', 23, timeout=5); ..."
    command = "python3 -c \"\nimport telnetlib\nt = telnetlib.Telnet('172.18.0.6', 23, timeout=5)\nprint(t.read_until(b'login:', timeout=3))\n\""
    output = "b'Ubuntu 22.04.5 LTS\\r\\ntelnet-target login:'\n"
    result = parse_step(command, output, 0)
    updates = dict(result.state_updates)
    assert updates.get("172.18.0.6", {}).get("service_telnet") is True
    assert updates.get("172.18.0.6", {}).get("port_23_open") is True
    assert updates.get("172.18.0.6", {}).get("is_alive") is True
    assert result.exploit is None


def test_telnetlib_shell_access_success():
    # Successful login: login prompt seen and LLM ran 'id' after authenticating
    command = "python3 -c \"\nimport telnetlib\nt = telnetlib.Telnet('172.18.0.6', 23, timeout=5)\nt.read_until(b'login:')\nt.write(b'admin\\n')\nt.read_until(b'Password:')\nt.write(b'admin123\\n')\nprint(t.read_until(b'$', timeout=3).decode())\nt.write(b'id\\n')\nprint(t.read_until(b'$', timeout=3).decode())\n\""
    output = "Ubuntu 22.04 LTS\ntelnet-target login: admin\nPassword: \nWelcome to Ubuntu 22.04\nadmin@telnet-target:~$ uid=1000(admin) gid=1000(admin) groups=1000(admin)\n"
    result = parse_step(command, output, 0)
    updates = dict(result.state_updates)
    assert updates.get("172.18.0.6", {}).get("service_telnet") is True
    assert updates.get("172.18.0.6", {}).get("shell_access") is True
    assert result.exploit is not None
    assert result.exploit.host == "172.18.0.6"
    assert result.exploit.vulnerability == "telnet_weak_credentials"


def test_telnetlib_no_login_prompt_returns_empty():
    command = "python3 -c \"import telnetlib; t = telnetlib.Telnet('172.18.0.6', 23, timeout=5)\""
    result = parse_step(command, "b''", 0)
    assert result.state_updates == []
    assert result.exploit is None


# ── Rejected commands return empty result ────────────────────────────────────

def test_rejected_command_returns_empty():
    # executor rejects binaries not in ALLOWED_BINARIES; exit_code is -1
    command = "timeout 5 telnet 172.18.0.6 23 | head -20"
    output = "[REJECTED] 'timeout' is not in the allowed binary set. Allowed: nmap, hydra, ..."
    result = parse_step(command, output, -1)
    assert result.state_updates == []
    assert result.exploit is None


# ── Unrecognised commands return empty result ─────────────────────────────────

def test_unrecognised_command_returns_empty():
    result = parse_step("ip addr show", "172.18.0.7/16 ...\n", 0)
    assert result.state_updates == []
    assert result.exploit is None
