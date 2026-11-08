"""실제 자식이 끝나기 전 부모 종료가 G를 먼저 풀지 않는지 검증한다."""
from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(sys.platform != "linux", reason="Linux FD와 signal 회귀")
@pytest.mark.parametrize("termination", [signal.SIGINT, signal.SIGTERM, signal.SIGKILL])
def test_parent_termination_keeps_g_until_own_child_finishes(tmp_path, termination):
    lock = tmp_path / "G"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    script = r"""
import fcntl,sys,json
from pathlib import Path
from kor_travel_docker_manager.services import service_release_compose as module
lock,ready,release=map(Path,sys.argv[1:])
module.GLOBAL_MUTATION_LOCK_PATH=lock
child = "import time;from pathlib import Path;Path(%r).touch();end=time.monotonic()+12;\nwhile not Path(%r).exists() and time.monotonic()<end:time.sleep(.02)" % (str(ready),str(release))
with lock.open('w') as handle:
    fcntl.flock(handle,fcntl.LOCK_EX)
    result,signals=module._run_mutation([sys.executable,'-c',child],capture_output=True,text=True,check=False)
    print(json.dumps({'exit':result.returncode,'signals':signals}))
"""
    parent = subprocess.Popen([sys.executable, "-c", script, str(lock), str(ready), str(release)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    def busy():
        with lock.open('r') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
    try:
        deadline = time.monotonic() + 8
        while not ready.exists() and time.monotonic() < deadline and parent.poll() is None:
            time.sleep(.02)
        assert ready.exists(), parent.communicate(timeout=1)
        os.kill(parent.pid, termination)
        if termination != signal.SIGKILL:
            time.sleep(.1)
            assert parent.poll() is None
        else:
            assert parent.wait(timeout=3) == -signal.SIGKILL
        assert busy(), "own Compose child is still running but G was released"
        release.touch()
        if termination != signal.SIGKILL:
            stdout, stderr = parent.communicate(timeout=5)
            assert parent.returncode == 0, stderr
            assert signal.Signals(termination).name in stdout
        deadline = time.monotonic() + 5
        while busy() and time.monotonic() < deadline:
            time.sleep(.02)
        assert not busy()
    finally:
        release.touch()
        if parent.poll() is None:
            try:
                parent.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                parent.kill()
                parent.communicate(timeout=3)
