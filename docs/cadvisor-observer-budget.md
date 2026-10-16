# cAdvisor 관측 비용 설정 지속화

현재 운영자가 확인한 cAdvisor 인자를 canonical Compose에 지속화한다.
기존 `--logtostderr`·`--docker_only=true`·port 보간 인자를 보존하고 다음 두 인자만 더한다.

- `--housekeeping_interval=30s`
- `--disable_metrics=percpu,sched,tcp,udp,disk,diskIO,hugetlb,referenced_memory,cpu_topology,resctrl`

CPU 우선 요구에 따라 10~15초 예시보다 확인 빈도가 낮은 기존30초를 유지한다.
개별 host systemd 추적 제한과 root aggregate 관측은 같은 의미가 아니므로,
`docker_only=true`를 root aggregate CPU가 반드시0이라는 주장으로 해석하지 않는다.

다른 서비스·healthcheck·resources·이미지·SDK는 변경하지 않는다. source만
지속화하는 작업이고 현재 runtime에 두 값이 이미 같게 적용됐다는 운영자 확인에
따르면 이 PR 때문에 새 이미지나 재시작이 필요하지 않다. agent는 운영을 조회하지 않았다.

관련 source회귀·Ruff는 완료했고 CI와 두 독립 리뷰는 후속이다. 실제 CPU 결과는 운영자의
별도 같은 구성·충분한 안정화 관측으로 판정하며, 초기 startup 표본을 개선으로
단정하지 않는다. 제외한 metric family는 이 구성에서 관측할 수 없다는 제한을
운영 측정 해석에 남긴다.


## 실제 source 검증

변경 전 새 정책 회귀2개가 실패했고, 변경 후 cAdvisor 및 기존Prometheus·Compose
보호 관련25개가 통과했다(291개 deselect,5.83초). Ruff와 diff 공백 검사도 통과했다.
새 정책을 이용한 자체 음성대조는 중복주기/더짧은충돌주기/기존docker_only누락/
잘못된metric범위4개를 거절했다. 원모형은 제품 테스트 수에 합산하지 않는다.

전체 Compose의 효과를 비교해 두 인자만 추가됐고, cAdvisor의 다른키와 다른서비스는
동일함을 확인했다. source원문도 두 인자와 설명주석2줄 추가 외 기존줄은 그대로다.
전체 backend suite나 frontend검증은 이 작업에서 재실행하지 않았고 frontend tree는
기준main과 같다. 실제 SDK/Docker/DB/provider/운영은 agent가 실행하지 않았다.
새 CI와 root 운영 측정 결과는 별도 근거로 판정한다.
