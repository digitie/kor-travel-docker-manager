"""location 정지 진단 수집 — 다음 정지 때 원인을 볼 자료를 파일로 남긴다(감사 §4 #19).

감사(2026-11-23 §2.4·§2.6)는 odroid weather location의 schedule·sensor가 기록 없이 3시간 넘게 멈춘
것을 보았지만 원인을 규명하지 못했다 — "다음 발생 때 daemon 스택 덤프 필요". 그래서 정지 경보가
울리는 순간 다음을 남긴다.

- **py-spy**(있을 때): daemon과 그 location code-server의 Python 프로세스마다 `py-spy dump --pid`.
  py-spy는 ptrace로 잠깐 멈춰 읽고 떼어 낸다 — 프로세스를 죽이거나 재시작하지 않는다. 대상 Dagster
  이미지에는 faulthandler·SIGUSR 처리기가 없어(2026-11-24 확인) signal을 보내면 기본 동작(종료)이
  된다. 그래서 signal은 보내지 않는다.
- **/proc 스레드 상태**(항상): 스레드마다 이름·상태(R/S/D)·커널 대기 지점(wchan)·커널 스택(root일 때).
  py-spy가 없어도 "어느 스레드가 무엇을 기다리는가"의 커널 쪽은 남는다.
- **docker 로그 꼬리**: daemon과 code-server의 최근 로그.

수집은 읽기만 한다. 디렉터리는 0700, 파일은 0600이다(로그에 내부 경로가 섞일 수 있다).
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kor_travel_docker_manager.services.plane_watchdog_probe import (
    Runner,
    container_processes,
    dagster_containers,
    location_names,
)

_PY_SPY_TIMEOUT_SECONDS = 30
_LOG_TIMEOUT_SECONDS = 30
_MAX_KERNEL_STACK_BYTES = 4096


@dataclass
class CaptureResult:
    location: str
    directory: str
    captured_at: str
    py_spy: str  # used | missing | failed
    containers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "location": self.location,
            "directory": self.directory,
            "captured_at": self.captured_at,
            "py_spy": self.py_spy,
            "containers": list(self.containers),
            "errors": list(self.errors),
        }


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")[:80] or "location"


def _write(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def _read(path: Path, limit: int | None = None) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"<읽을 수 없음: {exc.strerror or exc}>"
    if limit is not None:
        data = data[:limit]
    return data.decode("utf-8", "replace").strip()


def thread_report(pid: int, proc_root: Path = Path("/proc")) -> str:
    """스레드마다 tid·이름·상태·wchan·커널 스택. 읽기만 한다."""

    lines = [f"pid {pid}: {_read(proc_root / str(pid) / 'cmdline').replace(chr(0), ' ')}"]
    task_root = proc_root / str(pid) / "task"
    try:
        tids = sorted(int(name) for name in os.listdir(task_root) if name.isdigit())
    except OSError as exc:
        return "\n".join([*lines, f"<task 목록을 읽을 수 없음: {exc.strerror or exc}>"])
    for tid in tids:
        base = task_root / str(tid)
        stat = _read(base / "stat")
        # stat: "tid (comm) S ..." — comm에 공백·괄호가 들어갈 수 있어 마지막 ')' 뒤를 본다.
        state = stat.rpartition(")")[2].split()[0] if ")" in stat else "?"
        lines.append(
            f"- tid {tid} comm={_read(base / 'comm')!r} state={state} wchan={_read(base / 'wchan')!r}"
        )
        stack = _read(base / "stack", _MAX_KERNEL_STACK_BYTES)
        if stack and not stack.startswith("<"):
            lines.extend(f"    {frame}" for frame in stack.splitlines())
    return "\n".join(lines)


def _location_containers(location: str, runner: Runner) -> dict[str, list[tuple[int, str]]]:
    """daemon과, 명령줄에 location 이름(`-m`·`--location-name`)이 보이는 code-server."""

    chosen: dict[str, list[tuple[int, str]]] = {}
    for container in dagster_containers(runner):
        processes = container_processes(container, runner)
        is_daemon = "dagster-daemon" in container or any("dagster-daemon" in args for _, args in processes)
        if is_daemon or any(location in location_names(args) for _, args in processes):
            chosen[container] = processes
    return chosen


def _resolve_py_spy(configured: str) -> str | None:
    if configured:
        return configured if os.access(configured, os.X_OK) else None
    return shutil.which("py-spy")


def capture_location_stall(
    location: str,
    directory: Path,
    *,
    py_spy: str = "",
    log_tail_minutes: int = 60,
    runner: Runner = subprocess.run,
    proc_root: Path = Path("/proc"),
    now: _dt.datetime | None = None,
    context: dict[str, object] | None = None,
) -> CaptureResult:
    moment = (now or _dt.datetime.now(_dt.UTC)).astimezone(_dt.UTC)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / f"{stamp}-{_slug(location)}"
    target.mkdir(mode=0o700)
    executable = _resolve_py_spy(py_spy)
    result = CaptureResult(
        location=location, directory=str(target), captured_at=moment.isoformat().replace("+00:00", "Z"),
        py_spy="missing" if executable is None else "used",
    )
    try:
        containers = _location_containers(location, runner)
    except Exception as exc:  # noqa: BLE001 - 진단은 경보 경로를 막지 않는다
        containers = {}
        result.errors.append(f"컨테이너 목록: {exc}")
    result.containers = sorted(containers)
    for container, processes in sorted(containers.items()):
        python_pids = [pid for pid, args in processes if "python" in args]
        for pid in python_pids:
            _write(target / f"threads-{_slug(container)}-{pid}.txt", thread_report(pid, proc_root))
            if executable is None:
                continue
            try:
                dumped = runner([executable, "dump", "--pid", str(pid)], capture_output=True, text=True,
                                check=False, timeout=_PY_SPY_TIMEOUT_SECONDS, cwd="/")
                body = (dumped.stdout or "") + (f"\n[stderr]\n{dumped.stderr}" if dumped.stderr else "")
                if dumped.returncode != 0:
                    result.py_spy = "failed"
                    result.errors.append(f"py-spy {container}/{pid}: exit {dumped.returncode}")
            except (OSError, subprocess.SubprocessError) as exc:
                body = f"<py-spy 실행 실패: {exc}>"
                result.py_spy = "failed"
                result.errors.append(f"py-spy {container}/{pid}: {exc}")
            _write(target / f"py-spy-{_slug(container)}-{pid}.txt", body)
        try:
            logs = runner(["docker", "logs", "--since", f"{int(log_tail_minutes)}m", "--tail", "3000",
                           "--timestamps", container], capture_output=True, text=True, check=False,
                          timeout=_LOG_TIMEOUT_SECONDS, cwd="/")
            _write(target / f"docker-logs-{_slug(container)}.txt",
                   (logs.stdout or "") + (logs.stderr or ""))
        except (OSError, subprocess.SubprocessError) as exc:
            result.errors.append(f"docker logs {container}: {exc}")
    summary = {**result.as_dict(), "context": context or {}}
    _write(target / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
    return result


# ---------------------------------------------------------------------------
# fd 누수 진단 — `dagster_fd_usage` 경보 때(감사 §4 #3, n150 PinVi·concierge fd 고갈의 원인 미확정)
# ---------------------------------------------------------------------------

#: 함께 기록할 Python 패키지. 당시 가설: grpcio 1.84.0, IO 지연 조건, concierge와 공통 요소.
FD_PACKAGES = ("grpcio", "dagster", "dagster-postgres", "dagster-graphql", "protobuf", "psycopg2-binary",
               "psycopg2", "sqlalchemy", "kortravelcommon", "kor-travel-common")

_TCP_STATES = {
    "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1", "05": "FIN_WAIT2",
    "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING",
}
_UNIX_STATES = {"01": "UNCONNECTED", "02": "CONNECTING", "03": "CONNECTED", "04": "DISCONNECTING"}


def _hex_address(text: str) -> str:
    """`/proc/net/tcp` 주소(`0100007F:3039`, IPv6는 32자리)를 `127.0.0.1:12345`로."""

    host, _, port = text.partition(":")
    try:
        number = int(port, 16)
        raw = bytes.fromhex(host)
    except ValueError:
        return text
    if len(raw) == 4:
        return f"{'.'.join(str(b) for b in reversed(raw))}:{number}"
    if len(raw) == 16:
        # 4바이트 단위 little-endian 묶음이다.
        words = b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4))
        return f"[{ipaddress.IPv6Address(words)}]:{number}"
    return text


def socket_table(pid: int, proc_root: Path = Path("/proc")) -> dict[str, dict[str, str]]:
    """inode → {proto, local, remote, state}. 그 프로세스의 network namespace(`/proc/<pid>/net`)를 본다."""

    table: dict[str, dict[str, str]] = {}
    for proto in ("tcp", "tcp6", "udp", "udp6"):
        try:
            lines = (proc_root / str(pid) / "net" / proto).read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10:
                continue
            table[fields[9]] = {"proto": proto, "local": _hex_address(fields[1]),
                                "remote": _hex_address(fields[2]),
                                "state": _TCP_STATES.get(fields[3], fields[3]) if proto.startswith("tcp") else "-"}
    try:
        lines = (proc_root / str(pid) / "net" / "unix").read_text(encoding="utf-8", errors="replace").splitlines()[1:]
    except OSError:
        lines = []
    for line in lines:
        fields = line.split()
        if len(fields) < 7:
            continue
        table[fields[6]] = {"proto": "unix", "local": fields[7] if len(fields) > 7 else "(이름 없음)",
                            "remote": "-", "state": _UNIX_STATES.get(fields[5], fields[5])}
    return table


def fd_inventory(pid: int, proc_root: Path = Path("/proc")) -> dict[str, Any]:
    """열린 fd를 종류별로 세고 소켓은 원격 주소·상태로, 파일은 경로로 묶는다. 읽기만 한다."""

    kinds: Counter[str] = Counter()
    files: Counter[str] = Counter()
    sockets: Counter[str] = Counter()
    unresolved = 0
    table = socket_table(pid, proc_root)
    fd_dir = proc_root / str(pid) / "fd"
    for name in os.listdir(fd_dir):
        try:
            target = os.readlink(fd_dir / name)
        except OSError:
            kinds["(사라짐)"] += 1
            continue
        if target.startswith("socket:["):
            kinds["socket"] += 1
            entry = table.get(target[len("socket:["):-1])
            if entry is None:
                unresolved += 1
                sockets["(net 표에 없음 — 다른 namespace·netlink 등)"] += 1
            else:
                sockets[f"{entry['proto']} {entry['state']} {entry['local']} -> {entry['remote']}"] += 1
        elif target.startswith("pipe:["):
            kinds["pipe"] += 1
        elif target.startswith("anon_inode:"):
            kinds[target] += 1
        elif target.startswith("/dev/"):
            kinds["device"] += 1
            files[target] += 1
        else:
            kinds["file"] += 1
            files[target] += 1
    status = _read(proc_root / str(pid) / "status")
    threads = next((line.split()[1] for line in status.splitlines() if line.startswith("Threads:")), "?")
    return {
        "pid": pid, "open_fds": sum(kinds.values()), "threads": threads,
        "kinds": dict(kinds.most_common()), "sockets": dict(sockets.most_common(40)),
        "unresolved_sockets": unresolved, "files": dict(files.most_common(30)),
        "limits": next((line for line in _read(proc_root / str(pid) / "limits").splitlines()
                        if line.startswith("Max open files")), ""),
    }


def package_versions(container: str, executable: str, runner: Runner = subprocess.run) -> dict[str, str]:
    """그 프로세스와 같은 인터프리터로 `docker exec`해 패키지 버전을 읽는다(새 프로세스 하나, 대상은 건드리지 않음)."""

    script = (
        "import importlib.metadata as m, json, sys\n"
        "out = {'python': sys.version.split()[0]}\n"
        f"for name in {list(FD_PACKAGES)!r}:\n"
        "    try:\n"
        "        out[name] = m.version(name)\n"
        "    except m.PackageNotFoundError:\n"
        "        pass\n"
        "print(json.dumps(out))\n"
    )
    try:
        completed = runner(["docker", "exec", container, executable, "-c", script], capture_output=True,
                           text=True, check=False, timeout=_LOG_TIMEOUT_SECONDS, cwd="/")
        if completed.returncode == 0:
            return json.loads((completed.stdout or "").strip().splitlines()[-1])
        return {"error": f"exit {completed.returncode}: {(completed.stderr or '').strip()[-200:]}"}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return {"error": str(exc)}


def capture_fd_usage(
    container: str,
    directory: Path,
    *,
    runner: Runner = subprocess.run,
    proc_root: Path = Path("/proc"),
    now: _dt.datetime | None = None,
    context: dict[str, object] | None = None,
) -> CaptureResult:
    """`dagster_fd_usage` 경보 때: 컨테이너 안 `dagster api grpc`(없으면 fd가 가장 많은) 프로세스의 fd 내역."""

    moment = (now or _dt.datetime.now(_dt.UTC)).astimezone(_dt.UTC)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    phase = str(((context or {}).get("auto_restart") or {}).get("phase") or "")  # type: ignore[union-attr]
    name = f"{moment.strftime('%Y%m%dT%H%M%SZ')}-fd-{_slug(container)}" + (f"-restart-{_slug(phase)}" if phase else "")
    target = directory / name
    for attempt in range(2, 20):
        try:
            target.mkdir(mode=0o700)
            break
        except FileExistsError:
            # 같은 초에 경보 진단과 재시작 전 진단이 겹칠 수 있다(Manager 재시작 직후).
            target = directory / f"{name}-{attempt}"
    else:
        target.mkdir(mode=0o700)
    result = CaptureResult(location=container, directory=str(target),
                           captured_at=moment.isoformat().replace("+00:00", "Z"), py_spy="n/a",
                           containers=[container])
    processes = [(pid, args) for pid, args in container_processes(container, runner) if "python" in args]
    grpc = [item for item in processes if "dagster api grpc" in item[1]]
    chosen = grpc or processes
    reports = []
    for pid, args in chosen:
        try:
            inventory = fd_inventory(pid, proc_root)
        except OSError as exc:
            result.errors.append(f"fd {pid}: {exc.strerror or exc}")
            continue
        inventory["args"] = args[:300]
        executable = args.split()[0] if args else "python"
        inventory["packages"] = package_versions(container, executable, runner)
        reports.append(inventory)
        lines = [f"pid {pid}: {args[:300]}", f"threads {inventory['threads']}, open fds {inventory['open_fds']}",
                 inventory["limits"], "", "[종류]"]
        lines += [f"  {count:6d} {kind}" for kind, count in inventory["kinds"].items()]
        lines += ["", "[소켓: proto 상태 local -> remote]"]
        lines += [f"  {count:6d} {key}" for key, count in inventory["sockets"].items()]
        lines += ["", "[파일]"] + [f"  {count:6d} {key}" for key, count in inventory["files"].items()]
        lines += ["", "[패키지]"] + [f"  {key} {value}" for key, value in inventory["packages"].items()]
        _write(target / f"fd-{_slug(container)}-{pid}.txt", "\n".join(lines) + "\n")
    if not reports and not result.errors:
        result.errors.append("python 프로세스를 찾지 못했습니다")
    summary = {**result.as_dict(), "context": context or {}, "processes": reports}
    _write(target / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
    return result


def count_captures(directory: Path) -> int:
    try:
        return sum(1 for entry in directory.iterdir() if entry.is_dir())
    except OSError:
        return 0


def list_captures(directory: Path, limit: int = 10) -> Sequence[str]:
    try:
        names = sorted((entry.name for entry in directory.iterdir() if entry.is_dir()), reverse=True)
    except OSError:
        return []
    return names[:limit]


__all__ = [
    "CaptureResult",
    "capture_fd_usage",
    "capture_location_stall",
    "count_captures",
    "fd_inventory",
    "list_captures",
    "package_versions",
    "socket_table",
    "thread_report",
]
