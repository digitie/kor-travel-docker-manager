# cAdvisor 관측 비용 설정

## 최종 범위

기존 `--logtostderr`·`--docker_only=true`·port 보간 인자는 그대로 두고
`--housekeeping_interval=30s`와 다음 `--disable_metrics`를 명시한다.

`percpu,sched,tcp,udp,disk,diskIO,hugetlb,referenced_memory,cpu_topology,resctrl,memory_numa,advtcp,process,cpuset`

CPU 우선 요구에 따라 10~15초 예시보다 확인 빈도가 낮은 기존30초를 유지한다.
다른 서비스·healthcheck·resources·이미지·SDK는 변경하지 않는다. canonical
healthcheck는 원래의 port12103 보간과 `/healthz`를 그대로 유지한다.

## 기본 off 보존과 적용 단계

초기1016c3c는 운영자가 확인한10항목을 source에 그대로 지속화했다. 그러나
`disable_metrics`는 기본집합에 추가하는 옵션이 아니라 전체집합을 교체한다.
v0.52.1의 기본off11개 중 빠졌던 `memory_numa,advtcp,process,cpuset`을 뒤에
추가해 최종14개로 보강했다. 기존10항목과 기본off11개를 모두 보존하는 합집합이다.
[공식 기본집합](https://github.com/google/cadvisor/blob/v0.52.1/cmd/cadvisor.go#L74-L94),
[Set의 교체 동작](https://github.com/google/cadvisor/blob/v0.52.1/container/factory.go#L132-L144).

따라서 초기10항목 단계의 '현재runtime과 같으므로 재시작 불필요'는 **최종14항목
적용에는 해당하지 않는다**. 실제 기존runtime10→최종14 변경은 root가 실제binary
버전·지원항목을 확인한 뒤 수행한다. root 계획은 실제health URL12103 정합과 함께
단1회 clone/recreate로 적용하는 것이며 agent는 source만 작성한다. 실제Config,
설정별CPU, 재생성성공은 별도 운영 증거이며 이 source 결과로 성공 처리하지 않는다.

## 관측 해석

개별 host systemd 추적 제한과 root aggregate 관측은 같은 의미가 아니다.
`docker_only=true`를 root aggregate CPU가 반드시0이라는 주장으로 해석하지 않는다.
기본 동적 housekeeping은 true/max60초이며, `30s` 하나가 모든 관측을 고정30초로
만들거나 exporter의 전체 CPU를0으로 보장하지 않는다.
[공식 동적 주기 기본값](https://github.com/google/cadvisor/blob/v0.52.1/manager/manager.go#L61-L64).
제외한 metric family는 이 구성에서 관측할 수 없다는 제한을 남긴다.

## 실제 source 검증

초기 정책은 변경 전2FAIL→관련25PASS/291deselect/5.83초였다. 최종 기본off
보존 회귀는 초기10항목에서1FAIL/2PASS를 보존했고, 보강 후 관련26PASS/
291deselect/5.89초였다. Ruff와 diff 공백 검사도 통과했다. 자체음성4변형은
제품 테스트 수에 합산하지 않는다.

전체 Compose의 효과는 두 인자만 추가됐고 cAdvisor 다른키와 다른서비스는 동일하다.
source원문도 두 인자·관련주석 외 기존줄은 그대로다. 전체 backend suite와
frontend검증은 재실행하지 않았고 frontend tree는 기준main과 같다.
실제 SDK/Docker/DB/provider/SSH/socket/private/운영은 agent가 실행·열람하지 않았다.
두 독립리뷰·새CI·merge·root 운영지원확인/적용/CPU측정은 후속이며,
초기 startup이나 source검증만으로 CPU 개선을 확정하지 않는다.
