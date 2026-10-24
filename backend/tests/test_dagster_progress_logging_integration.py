"""공용 instance의 진행 로그·실패·확정 진척을 실제 SDK와 PostgreSQL에서 검증한다."""

from __future__ import annotations

import json

from test_dagster_shared_storage_integration import (
    _DB_INIT,
    _MIGRATE,
    _RUN_TIMEOUT,
    _one_shot,
    _Plane,
    _run,
    _wait_healthy,
)
from test_dagster_shared_storage_integration import isolated_plane as isolated_plane

_PROGRESS_PROBE = """
import json, logging, os
from dagster import AssetObservation, DagsterInstance, Failure, OpExecutionContext, job, op
from dagster._core.instance.ref import InstanceRef

@op(config_schema={"fail": bool})
def persist_progress(context: OpExecutionContext):
    context.log.debug("fixture_debug_payload")
    context.log.info("fixture_committed_progress count=3")
    context.log.warning("fixture_warning_summary")
    logging.getLogger("fixture_unmanaged_http_payload").warning("fixture_raw_secret_excluded")
    for index, name in enumerate(managed):
        logging.getLogger(name).info("fixture_managed_progress_%s", index)
    context.log_event(AssetObservation(asset_key="fixture_progress", metadata={"committed": 3}))
    if context.op_config["fail"]:
        raise Failure("fixture_storage_failure", allow_retries=False, metadata={"committed": 3})

@job
def progress_probe():
    persist_progress()

with DagsterInstance.get() as canonical:
    settings = canonical.get_settings("python_logs")
managed = settings["managed_python_loggers"]
cases = 0
for control in (False, True):
    overrides = {"python_logs": dict(settings, python_log_level="WARNING")} if control else None
    reference = InstanceRef.from_dir(os.environ["DAGSTER_HOME"], overrides=overrides)
    for fail in (False, True):
        with DagsterInstance.from_ref(reference) as instance:
            assert type(instance.event_log_storage).__name__ == "PostgresEventLogStorage"
            result = progress_probe.execute_in_process(
                instance=instance, raise_on_error=False,
                run_config={"ops": {"persist_progress": {"config": {"fail": fail}}}},
            )
            assert result.success is not fail
            run_id, saved_reference = result.run_id, instance.get_ref()
        with DagsterInstance.from_ref(saved_reference) as reopened:
            records = reopened.all_logs(run_id)
            messages = "\\n".join(record.user_message for record in records)
            assert ("fixture_committed_progress" in messages) is not control
            assert "fixture_warning_summary" in messages
            assert "fixture_debug_payload" not in messages
            assert "fixture_raw_secret_excluded" not in messages
            for index in range(len(managed)):
                assert (f"fixture_managed_progress_{index}" in messages) is not control
            observations = [record.dagster_event.event_specific_data.asset_observation
                            for record in records if record.is_dagster_event
                            and record.dagster_event.event_type_value == "ASSET_OBSERVATION"]
            assert len(observations) == 1 and observations[0].metadata["committed"].value == 3
            assert reopened.get_run_by_id(run_id).status.value == ("FAILURE" if fail else "SUCCESS")
        cases += 1
print(json.dumps({"cases": cases, "level": settings["python_log_level"], "managed": len(managed)}))
"""


def test_shared_progress_survives_failure_and_reopen(isolated_plane: _Plane) -> None:
    """INFO 진행과 기존 경고는 보존하고, DEBUG·미등록 Python 로그는 수집하지 않는다."""
    plane = isolated_plane
    _wait_healthy(plane.postgres)
    for service in (_DB_INIT, _MIGRATE):
        prepared = _one_shot(plane, service)
        assert prepared.returncode == 0, prepared.stdout[-2000:] + prepared.stderr[-2000:]
    probe = _run(
        *plane.compose, "run", "--rm", "--no-deps", "--entrypoint", "python", _MIGRATE,
        "-I", "-c", _PROGRESS_PROBE,
        env=plane.env, timeout=_RUN_TIMEOUT,
    )
    assert probe.returncode == 0, probe.stdout[-2000:] + probe.stderr[-4000:]
    result = json.loads(probe.stdout.strip().splitlines()[-1])
    assert result["cases"] == 4 and result["level"] == "INFO" and result["managed"] > 0
