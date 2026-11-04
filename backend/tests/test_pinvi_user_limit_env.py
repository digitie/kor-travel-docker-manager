"""실제 정본 Compose가 사용자별 상한 JSON을 손실 없이 전달한다."""

import json
import os
import subprocess

import pytest
import yaml
from test_pinvi_etl_env_wiring import _COMPOSE, _ROOT

_SETTING = "PINVI_RATE_LIMIT_USER_LIMIT_OVERRIDES"


@pytest.mark.parametrize(
    "raw",
    [None, "", '{"00000000-0000-0000-0000-000000000101":600,"00000000-0000-0000-0000-000000000102":4}'],
)
def test_pinvi_user_limit_json_survives_resolved_compose(raw: str | None) -> None:
    # 실제 서비스의 환경 바인딩만 렌더한다. 다른 필수 값과 daemon은 필요 없다.
    source = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    binding = source["services"]["pinvi-api"]["environment"]
    assert _SETTING in binding
    fragment = yaml.safe_dump(
        {"services": {"pinvi-api": {"image": "pinvi-contract-only", "environment": {_SETTING: binding[_SETTING]}}}}
    )
    environment = {key: value for key, value in os.environ.items() if key != _SETTING and not key.startswith("COMPOSE_")}
    environment["COMPOSE_PROJECT_NAME"] = "pinvi-user-limit-contract"
    if raw is not None:
        environment[_SETTING] = raw
    completed = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "-f", "-", "config", "--format", "json"],
        cwd=_ROOT, env=environment, input=fragment, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)["services"]["pinvi-api"]["environment"][_SETTING]
    assert actual == (raw or "{}")
    assert json.loads(actual) == json.loads(raw or "{}")
