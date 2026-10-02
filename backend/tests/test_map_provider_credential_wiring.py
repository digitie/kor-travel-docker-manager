"""Map provider 자격증명이 compose에서 **그것을 쓰는 서비스 전부**에 닿는지 센다.

키를 받는 서비스와 그 키를 실제로 쓰는 서비스가 어긋나면, 배포는 끝나는데 job 하나만
조용히 `ProviderCredentialMissing`으로 죽는다. 2026-09-18 n150에서 KREX go key와 OpiNet
scope 선택자가 그 모양으로 빠졌다 — 둘은 Map이 휴게소·주유소·유가를 kor-travel-transport
내부 export에서 읽게 되면서(2026-10) compose에서 사라졌고, 남은 사례가 서울 열린데이터광장
키다.

그래서 이름을 손으로 나열하지 않는다. **기준이 되는 키를 받는 서비스 집합을 문서에서
읽어**, 짝 키가 같은 집합에 닿는지를 센다.
"""

from __future__ import annotations

from pathlib import Path

_COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _services_declaring(name: str) -> set[str]:
    text = _COMPOSE.read_text(encoding="utf-8")
    services: set[str] = set()
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("  ") and not line.startswith("    ") and line.rstrip().endswith(":"):
            current = line.strip().rstrip(":")
        elif current is not None and line.strip().startswith(f"{name}:"):
            services.add(current)
    return services


#: 서울 열린데이터광장 인증키.
#:
#: data.go.kr과 **다른 포털이고 키도 다르다**(서울시 자체 발급). Map의 curated
#: fileData 4종 중 서울 책방만 이 키를 쓴다 — 종전 odcloud 원천이 404
#: `등록되지 않은 서비스 입니다`로 사라져 2026-09-19에 원천을 OA-21062로 옮겼다.
_SEOUL_OPEN_DATA_KEY = "KOR_TRAVEL_MAP_SEOUL_OPEN_DATA_API_KEY"


def test_the_seoul_open_data_key_reaches_every_service_that_runs_file_data() -> None:
    """fileData를 돌리는 서비스는 서울 열린데이터광장 키도 받아야 한다.

    data.go.kr 키 쪽을 기준으로 삼는 이유는 그쪽이 "이 서비스가 curated fileData를
    돌린다"는 선언이기 때문이다. 키를 안 넘기면 4종 중 서울 책방만 조용히
    `ProviderCredentialMissing`으로 죽는다 — 2026-09-18의 KREX go key, OpiNet scope와
    **같은 형태의 구멍**이다.
    """

    with_data_go_kr = _services_declaring("KOR_TRAVEL_MAP_DATA_GO_KR_SERVICE_KEY")
    assert with_data_go_kr, "data.go.kr 키를 받는 서비스가 하나도 없다"
    missing = sorted(with_data_go_kr - _services_declaring(_SEOUL_OPEN_DATA_KEY))
    assert not missing, f"{_SEOUL_OPEN_DATA_KEY}를 못 받는 서비스: {missing}"
