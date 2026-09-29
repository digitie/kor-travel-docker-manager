# 공용 Dagster 호스트 이미지 — webserver·daemon·storage migrate가 같은 이미지를 쓴다
# (platform-topology.md §7, dagster-shared-plan §3·§4).
#
# **재빌드가 버전을 움직이지 못하게 한다.** Dagster의 호환 정책은 "호스트(webserver/daemon)
# 버전 ≥ code-server 버전"이다. 호스트가 조용히 올라가면 code-server와 어긋나고, 반대로
# 내려가면 정책 밖이다. 그래서 설치는 해시까지 고정한 잠금본(requirements.txt)만 쓴다 —
# `--require-hashes`는 잠금본에 없는 패키지·다른 파일을 거부하고, `--no-deps`는 해석기가
# 잠금본 밖으로 나가는 길을 없앤다. 베이스도 digest로 고정한다(태그 하나로는 재빌드가
# 다른 Python을 받는다).
#
# build context는 이 디렉터리뿐이다 — 저장소 전체(frontend/node_modules 포함)를 보내지 않는다.
FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DAGSTER_HOME=/opt/dagster/dagster_home

COPY requirements.txt /tmp/dagster-host-requirements.txt
RUN pip install --no-deps --require-hashes --only-binary=:all: \
        -r /tmp/dagster-host-requirements.txt \
    && pip check \
    && rm /tmp/dagster-host-requirements.txt

# `DAGSTER_HOME`은 설정 전용(root 소유, `dagster.yaml`은 compose가 읽기 전용으로 붙인다).
# 로컬 쓰기(compute log·artifact)는 공용 `dagster.yaml`이 `/opt/dagster/state`로 보낸다 —
# 비-root 사용자가 쓸 수 있는 자리는 그곳 하나다.
RUN useradd --system --uid 10001 --user-group --home-dir /opt/dagster --no-create-home dagster \
    && mkdir -p /opt/dagster/dagster_home /opt/dagster/state \
    && chown dagster:dagster /opt/dagster/state

COPY --chmod=0755 storage-migrate.py /usr/local/bin/kor-travel-dagster-storage-migrate

USER dagster
WORKDIR /opt/dagster
