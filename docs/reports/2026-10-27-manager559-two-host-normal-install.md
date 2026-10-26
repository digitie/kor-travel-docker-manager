# Manager559 양 호스트 정상 설치와 공개 UI 검증

이 기록은 제품 PR559의 머지 소스 `aeb2d34ce276e5e219761a62cd4b1c8f39cad72a`와 검토 head `85101c68b375ddc09fca7e0e294f9ecc597200db`의 동일 tree `67f6ad1d53d6babe73bd680ccabd9883f6c9f3dd`에 한정한다. 제품 두 독립 리뷰·정확 CI backend3445 PASS/40 SKIP·frontend PASS·격리 후보 UI14 PASS는 설치 전 근거다. 후보 UI의 합류와 부적격 재조회는 수용했고 positive cache는 검증하지 않았다.

## 실제 설치

| 구분 | n150 | Odroid |
| --- | --- | --- |
| 공식 정상 installer | exit0, 21.01초 | exit0, 188.555초 |
| 설치 source | 435개 mode/blob exact | 435개 mode/blob exact |
| Backend/API·CLI | 새 M, health200 | 새 M, health200 |
| Frontend | 같은 58개 입력의 기존 산출물 유지 | M 58개 입력의 새 ARM 산출물 승격 |
| show host/max | n150/6 | odroid/5 |
| 공개 TLS 실제 브라우저 | 최종12 PASS | 최종12 PASS |

n150은 canonical wheel27개를 재사용했다. Odroid는 기존 native/pure wheel25개와 같은 canonical Poetry wheel을 새 NVMe cache26개로 준비했다. 원 cache·원 release·root clone의 HEAD/index/dirty 상태를 보존했다. 실제 설치는 기존 global mutation lock을 사용했으며, 외부 소유자가 잡은 첫 Odroid G 충돌은 exit2로 종료하고 자연 해제 후 정상 설치했다. 소유자를 중단하거나 락을 우회하지 않았다.

Odroid installer의 원 wrapper는 Docker Mounts 배열 순서만 달라 false였다. 원 false는 보존하고 destination별 mount row·CID·실제 파일 bytes의 동일성을 따로 확인했다. 순서 정렬 외 차이는 없으며 원 결과를 덮어쓰지 않았다.

## ARM frontend 준비와 승격

기존 Next14/React18 의존성을 새 M의 Next16.4.3/React19.3.1에 재사용하지 않았다. 새 NVMe source/cache/tmp에서 offline 의존성 준비가 219.747초 뒤 ENOTCACHED였고, 허용된 정본 registry와 lock 무결성으로 online 준비가 398.393초에 성공했다. 설치된 500개 package와 lock을 대조했다. 첫 npm 설정 중복 오류와 설치·빌드 합산 시간 예산 초과는 원형 보존했다. 합산 예산 초과는 제품 compile 실패라고 분류하지 않았다.

검증된 의존성을 재설치하지 않고 별도 build-only 실행을 사용했다. 새 build481.837초/unit484.601초, exit0이다. 실제 cgroup CPU1·메모리2560MiB·swap0·PID256, 설정900초/stop15초를 유지했다. 종료 뒤 cgroup 루트를 잘못 읽은 중간 진단값은 전부 미수용이다. 원 partial.next는 보존하고 입력58개가 동일한 fresh .next를 생성했다. 별도 복사본 제안은 실행 시작 뒤 도착했으므로 그 차이를 숨기지 않는다.

최종 산출물은 source58·manifest4·static17개/1,400,417B가 exact였다. 현 공개 backend build input은 RAM에서만 전달했고 새 후보용 주소를 넣지 않았다. 기존 G 안에서 frontend 하나만 정지·canonical 경로 교체·재시작했으며 기존 산출물을 보존했다. 새 frontend는 실제 HTTP 정적 파일17개의 bytes까지 일치한다. Backend·CLI·root env·plane과 다른 컨테이너는 이 승격으로 바뀌지 않았다.

## 공용 plane과 Weather 보존

새 Odroid prepared instance는 `a7d907c4d54024e2868c1c9db0e6d775a351bfc1d0001de378983e8a2c7bc9db`이고 현재 살아 있는 plane의 실제 bind bytes는 `c44486388779b092f3287576756ab953d4cf6d8b0593d1d22da7b70e75c709e1`다. APP alias가 새 source를 가리킨다는 이유로 실행 중 container의 bind inode도 바뀌었다고 판정하지 않았다. daemon/webserver의 실제 CID와 inside bytes를 보존했다. plane converge·재시작·Weather 이미지/컨테이너/CS 교체를 하지 않았다.

실제 기존 정책 max5·Geo1·Weather3·external2·slow1·WARNING을 확인했다. 저장된 selected policy 비교에서 기존17개 tag identity/limit과 python_logs는 같고 prepared 파일은 Concierge admin-region limit1 하나를 추가한다. 이 비교를 YAML 모든 경로의 semantic 동등성으로 넓히지 않는다.

frontend 교체 전후 고정 Weather9개 run의 상태와 Weather4개 container CID/image/StartedAt/restart는 모두 같았다. retention FAILURE는 교체 전부터 존재했다. 전체 active 조회에 새 QUEUED2개가 추가된 관측은 별도 보존하며 원인이나 기존 run 취소로 해석하지 않는다. job/instigator 시작·중단·취소·수정은 0회다.

## 공개 UI와 미완 경계

n150의 최종 새 세션12개 검사는 공개 TLS 로그인200·쿠키·현재 상태 조회200·실제 WS2frame·reload·로그아웃200·보호 조회401·후행 WS loop 부재·정리를 확인했다. Odroid는 공개 정적17개·로그인200·me200 뒤 추가 직접 containers 조회503으로 원12개 중2개에서 중단했다. 같은 세션의 정리 logout200와 보호401, 세션 철회·브라우저/profile 부재는 확인했다. 이 원 실패를 정상 설치나 loopback auth PASS로 치환하지 않는다.

실제 UI 원장과 WS2frame이 먼저 수렴한 뒤 같은 strict API200 검사를 수행하도록 원 블록의 순서만 옮겼다. 추가 sleep·retry·시간 상한 증가·단언 삭제는 없으며 W 독립 소스 검토 뒤 Odroid 공개12가 모두 통과했다. 원503의 발생 분기는 직접 계측하지 않았으므로 이 성공으로 단일 원인을 확정하거나 제품 수정 효과를 주장하지 않는다. 최초 실패에서 관측된 후행 socket1도 별도 원형 보존한다.

추가 공개 API의 현재 값 대조에서는 n15039행/Odroid43행을 반환했고, 각 host의 실행 중19개 (이름, CID, 상태) 집합이 정본 managed+observed 범위의 실제 Docker와 모두 같았다. 같은19개 모두 finite 수치 메트릭과 stats_available을 확인했고 Docker의 전후 목록도 같았다. 각 검증 자체 세션은 login/logout1회와 보호401로 정리했다. remote HTTP의 up/down은 원격 Docker 상태라고 해석하지 않는다.

첫 추가 대조기의 sparse Container.name은 None이어서 r1/r2가 실패했다. 제품은 이미 Names fallback을 올바르게 사용하고 있었다. 진단기만 같은 정본 필드 해석으로 고치고 정확 함수 AST의 sparse 양성 순수 검증 뒤 r3의 실제 일치를 확인했다. 원 두 진단 실패를 제품 실패로 분류하지 않는다.

별도 fresh-process 읽기 전용 collector는43행을4.350초에 반환했다. compose YAML parse1.367초·inventory0.054초·remote0.872초·cached metrics19회0.002초였고 import10.057초는 별도다. 현재 값 대조의 공개 GET은 n1500.624초/Odroid5.333초였다. 이 값은 원 실패 request의 비용이나 503 분기 직접 측정이 아니다. 공유 follower5초와 leader의 기존 I/O 경계는 바꾸지 않았다. YAML 최적화나 CPU 저감은 별도 제품 후속이며 이번 설치에 섞지 않았다.

외부 Transport139의 실제 소비와 이후 명시적 plane/code-server 수렴은 이 Manager baremetal 설치와 별도다. CPU 저감·양 host cache 적격성·완전한 외부 G 원자성은 주장하지 않는다.

## 감사 근거

파일명은 로컬 감사 보관소의 정본을 가리키며 비밀·본문·쿠키를 이 문서에 복제하지 않는다.

| 근거 | SHA256 |
| --- | --- |
| `manager559-aeb2-n150-normal-install-t-1793050997455559259.json` | `c9f0338b240c47845a4bbaa623124b71834ee77f840683bebcfc782b6321f659` |
| `manager559-aeb2-odroid-normal-actual-acceptance-t-r1.json` | `6f335a67e360021808e900a4737b2e4a1d6ec967c63e766073ad2565be410868` |
| `manager559-M-arm-frontend-w-build-and-handoff-acceptance-r1.json` | `55dde5a1b7a9b048e87a57f1a40abda07d830f4f5e3dac6b1e9dfaaeebc273e0` |
| `manager559-odroid-frontend-promotion-actual-t-1793053191428640781.json` | `75047dae373427251fb7d82acec411b4c2ca7afa755c6c9809166c497736e1bf` |
| `manager559-aeb2-final-n150-public-ui-t-1793053405426982997.json` | `97d15f40b3f8c0e52f3a5910b379c9414d88d90f46a3c26fb52bd8de474edd16` |
| `manager559-odroid-public-TLS12-actual-t-1793053748155864963.json` | `6980d340315e46f8a1025eda44281c5602ef119e3cf018e1e07bd14fabdff7dd` |
| `manager559-odroid-public-TLS12-ready-actual-t-1793054283273973961.json` | `372b867777250b194126cbb7cf2c9c409b3804ab145437c95e43f2bd6e9f2a33` |
| `manager559-odroid-public-TLS12-r2-w-independent-saved-actual-factcheck-r1.json` | `8a2fed3bfef08d7e126fc00d92591f54a97d0140c288bec55d902916a2f4e751` |
| `manager559-public-current-Docker-canonical-row-equality-r3-t-1793054519067423041.json` | `b210cc0ae9915b17dea5a65e4331771d8ee931298ce8b83f4a9baa01c49da592` |
| `manager559-weather-runs-frontend-before-readonly-t-r1.json` | `9e4ab51bb6018c80201e85532e4c8cb830819e21036cbed876ea11f26b36ca17` |
| `manager559-weather-runs-frontend-after-readonly-t-r2.json` | `5e98cc8a36d860341ebd6cb49cb5dbf036682938f1f7de9a8c35958d3f28a23a` |
