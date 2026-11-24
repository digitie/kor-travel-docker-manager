# 공용 Dagster 진행 로그

## 설정 소유권과 관측 범위

공용 instance의 로그 수준 정본은 `config/dagster-shared/dagster.yaml`의 `python_logs`다.
기존 WARNING 설정은 각 프로젝트의 `context.log.info`까지 버렸다. 이제 INFO로 진행을
보존하므로 공용 plane에 연결된 모든 location이 같은 정책을 받는다. 현재 location 목록은
`config/dagster-shared/workspace.*.yaml`에서 확인하며 별도 목록을 만들지 않는다.

`managed_python_loggers`는 기존 명시 목록을 유지한다. root·HTTP·provider logger 전체를
수집하지 않는다. 프로젝트는 건수·단계·경과 시간 같은 요약을 `context.log.info`로 남기고,
인증값·요청 URL·원문 응답을 진행 메시지에 넣지 않는다. Transport의 provider 로그 가림은
Transport가 계속 소유한다. 공용 라이브러리나 소비자 버전 변경은 필요 없다.

## 성공 판정과 저장 진척

로그 수준 변경은 수집 성공 판정이나 재시도를 바꾸지 않는다. 실패·미완료 판정은 해당
프로젝트가 `Failure`와 결과 metadata로 전달해야 한다. INFO 메시지가 있다는 이유로
수집·저장 성공으로 세지 않는다. 확정한 부분 진척은 필요한 경우 `AssetObservation` 같은
구조화 이벤트에도 남겨 다음 SQL 실패와 로그 수준에 관계없이 복구 근거를 보존한다.

`backend/tests/test_dagster_progress_logging_integration.py`는 정본 설정·고정 SDK 이미지와
격리 PostgreSQL에서 정상/실패 각각의 INFO·기존 경고·명시 Python logger를 실행한다.
instance를 다시 열어 진행과 확정 진척·종료 상태가 남는지 확인하고, WARNING 대조군에서는
INFO가 사라지는지 검증한다. DEBUG와 미등록 Python 로그는 수집하지 않는 것도 확인한다.
설정 마운트·내용 digest의 기존 회귀가 모든 carrier의 같은 정본 사용을 검증한다.

## 적용과 되돌림

이 변경은 소스 개선이며 사용자의 배포 대기 지시를 따른다. 운영 컨테이너·instigator·DB·
공용 daemon을 이 작업에서 변경하지 않는다. Transport 개선은
[PR123](https://github.com/digitie/kor-travel-transport/pull/123)으로 먼저 머지했다.

설정 파일만 교체해도 기존 `InstanceRef`는 이전 수준을 유지한다. 일괄 배포 담당자는
Manager의 기존 적용 절차([공용 plane](platform-topology.md#7-전환-중--공유-제어-평면-2026-09-19-결정))를
사용하고, 붙은 설정과 기존 `KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST`의 일치 및 새 instance의
실제 INFO 저장을 확인한다. digest는 기존 YAML 앵커 한 곳을 갱신하고 carrier가 공유한다.
새 게이트·별도 재시작 도구·프로젝트별 복사 설정을 추가하지 않는다.

기존 활성 run의 진행을 이 설정 변경만으로 복구했다고 판단하지 않는다. 배포 뒤 다음
정상 예약 실행의 진행·원천 성공·실제 저장을 확인하며 강제 전체 수집을 추가하지 않는다.
되돌릴 때는 설정과 파생 digest를 함께 이전 release로 복원하고 같은 기존 적용 절차를
사용한다. 이미 저장된 event와 애플리케이션 체크포인트를 삭제할 이유는 없다.

## 코드 서버별 실행 로그 보존

외부 상태 볼륨을 쓰는 코드 서버는 공용 Manager 유지보수 타이머로 로컬 실행 로그를
정리할 수 있다. Manager는 기존 호스트·프로젝트 배포 잠금을 확보한 뒤 등록된 Compose
역할과 볼륨을 확인한다. 검사 중에는 해당 프로젝트의 배포 잠금만 유지한다.
프로젝트의 Python 모듈이 종료 상태, 소유권, 만료 시각을
판단하고 집계 결과를 JSON으로 반환한다.

Transport 배포와 상태 이관 뒤 dry-run을 실행한다.

```sh
sudo /opt/kor-travel-docker-manager/backend/.venv/bin/python -I -m kor_travel_docker_manager.dagster_compute_log_retention --target transport
```

결과의 `ok`, `mode`, `eligible_files`, `skipped_runs`를 확인한다. 필요할 때
`--apply`로 한 번 실행하고 `removed_files`를 확인한다. 이후 이 release의
`deploy/systemd`에 있는 두 템플릿을 호스트에 설치하고
`ktdm-dagster-compute-log-retention@transport.timer`를 활성화한다.

다른 프로젝트는 `config/docker-targets.yml`의 `dagster_compute_log_retention`에
기존 core release 그룹에 속한 코드 서버 서비스, 컨테이너의 Python 경로와 모듈을 등록한다.
전용 볼륨은 [Manager의 상태 볼륨 정책](service-current-release.md#이후-교체와-다른-프로젝트-채택)을 사용한다. 모듈은 `--dry-run`과 `--apply`를 지원하고
stdout에 실행 모드와 성공 여부를 담은 버전 1 JSON 한 줄을 출력해야 한다.
코드 서버 이미지의 `/usr/bin/timeout`이 한 번 실행을 20분으로 제한한다. 정확한
대상에 대한 dry-run과 코드 서버 재생성 검증 뒤 해당 프로젝트의 타이머를 켠다.
