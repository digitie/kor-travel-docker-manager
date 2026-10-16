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

관련 source회귀·Ruff·CI와 두 독립 리뷰는 후속이다. 실제 CPU 결과는 운영자의
별도 같은 구성·충분한 안정화 관측으로 판정하며, 초기 startup 표본을 개선으로
단정하지 않는다. 제외한 metric family는 이 구성에서 관측할 수 없다는 제한을
운영 측정 해석에 남긴다.
