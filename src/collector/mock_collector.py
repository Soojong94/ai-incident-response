import random
from datetime import datetime, timedelta

_LOG_TEMPLATES = {
    "cpu": [
        "{ts} kernel: [CPU] Load average: 4.82 5.01 4.93 — threshold exceeded",
        "{ts} systemd[1]: cpu.slice: Task limit reached (2048/2048)",
        "{ts} kernel: [CPU] process 'java' consumed 98.4% CPU for 300s",
        "{ts} crond[1023]: WARN task took too long, CPU throttled",
        "{ts} nginx[2048]: upstream timed out (110: Connection timed out)",
        "{ts} java[3901]: GC overhead limit exceeded — full GC every 2s",
        "{ts} java[3901]: OutOfMemoryError: GC overhead limit exceeded",
        "{ts} kernel: [CPU] soft lockup - CPU#0 stuck for 23s!",
    ],
    "memory": [
        "{ts} kernel: Out of memory: Kill process 4521 (java) score 892",
        "{ts} kernel: oom_reaper: reaped process 4521 (java), now anon-rss:0kB",
        "{ts} java[3901]: java.lang.OutOfMemoryError: Java heap space",
        "{ts} systemd[1]: memory.limit: Memory cgroup out of memory",
        "{ts} kernel: mem_cgroup_out_of_memory: Kill process (httpd)",
        "{ts} app[5012]: FATAL malloc failed — cannot allocate 512MB",
        "{ts} kernel: [MEM] free: 128MB total: 16384MB used: 16256MB",
        "{ts} kernel: page allocation failure — order:3, mode:0x40cc0",
    ],
    "disk": [
        "{ts} kernel: EXT4-fs error (device sda1): ext4_find_entry: no space left",
        "{ts} kernel: [DISK] /dev/sda1: I/O error dev sda1 sector 18432701",
        "{ts} systemd[1]: var.mount: Disk quota exceeded",
        "{ts} logrotate[1234]: ERROR rotating logs: No space left on device",
        "{ts} postgres[5678]: could not write to file: No space left on device",
        "{ts} app[5012]: FATAL Cannot write log file: disk full",
        "{ts} df output: /dev/sda1 100% /",
        "{ts} kernel: [DISK] write error on /dev/sda — IO scheduler abort",
    ],
    "network": [
        "{ts} kernel: TCP: too many orphaned sockets",
        "{ts} kernel: nf_conntrack: table full, dropping packet",
        "{ts} nginx[2048]: connect() failed (110: Connection timed out) upstream",
        "{ts} app[5012]: ERROR database connection timeout after 30s",
        "{ts} kernel: [NET] eth0: transmit queue 0 timed out",
        "{ts} app[5012]: ERROR redis.exceptions.ConnectionError: timed out",
        "{ts} kernel: neighbour: arp_cache: neighbor table overflow",
        "{ts} app[5012]: WARN retrying connection (attempt 3/5)...",
    ],
    "http": [
        "{ts} nginx[2048]: [error] upstream returned 502",
        "{ts} nginx[2048]: [error] connect() failed (111: Connection refused) upstream",
        "{ts} app[5012]: ERROR NullPointerException at UserService.java:142",
        "{ts} app[5012]: ERROR Unhandled exception: 500 Internal Server Error",
        "{ts} nginx[2048]: [warn] 1024 worker_connections are not enough",
        "{ts} app[5012]: ERROR database pool exhausted (max=50 active=50)",
        "{ts} app[5012]: FATAL Uncaught exception — restarting worker",
        "{ts} nginx[2048]: [error] no live upstreams while connecting to upstream",
    ],
}

_NORMAL_LOGS = [
    "{ts} systemd[1]: Starting Daily apt upgrade and clean activities...",
    "{ts} sshd[2234]: Accepted publickey for ubuntu from 10.0.0.5",
    "{ts} cron[1023]: session opened for user root by (uid=0)",
    "{ts} kernel: [NET] eth0: renamed from vethb3f8c1a",
    "{ts} systemd[1]: Finished Rotate log files.",
]


def generate_mock_logs(metric_type: str, alarm_time: datetime, count: int = 30) -> list[str]:
    """알람 시점 기준 5분 전 로그 생성. 초반 2분은 정상, 이후 3분은 에러 중심."""
    metric_key = metric_type.lower()
    error_templates = _LOG_TEMPLATES.get(metric_key, _LOG_TEMPLATES["cpu"])

    logs = []
    start = alarm_time - timedelta(minutes=5)
    interval = timedelta(seconds=10)

    for i in range(count):
        ts = (start + interval * i).strftime("%b %d %H:%M:%S")
        elapsed_pct = i / count
        if elapsed_pct < 0.4:
            tmpl = random.choice(_NORMAL_LOGS)
        elif elapsed_pct < 0.7:
            tmpl = random.choice(error_templates) if random.random() < 0.5 else random.choice(_NORMAL_LOGS)
        else:
            tmpl = random.choice(error_templates)
        logs.append(tmpl.format(ts=ts))

    return logs


async def collect(alarm_data: dict) -> list[str]:
    metric_type = alarm_data.get("metric_type", "cpu")
    alarm_time_raw = alarm_data.get("alarm_time")
    try:
        alarm_time = datetime.fromisoformat(str(alarm_time_raw)) if alarm_time_raw else datetime.now()
    except (ValueError, TypeError):
        alarm_time = datetime.now()
    return generate_mock_logs(metric_type, alarm_time)
