# 복원 리허설 timeout과 현재 scratch 정리

## 문제와 변경

실제 리허설에서 로컬 subprocess의 600초 timeout 뒤에도 컨테이너의 pg_restore와
scratch DB backend가 계속 실행됐다. 기존 finally의 dropdb에는 --force가 없어 활성
연결을 종료하지 못했다. dump 삭제·dropdb 자체가 timeout이면 원래 복원 예외까지
가릴 수 있었고, finally의 findings는 원예외가 전파되면 CLI에 반환되지 않았다.

복원 명령은 컨테이너의 timeout으로 원래 --timeout 기간 뒤 TERM, 5초 뒤 KILL을
보낸다. 로컬 docker client에는 이 종료 여유를 포함해 10초를 더 준다. 유효하지 않은
0·음수·bool timeout은 DB 조회 전에 거부한다. timeout 실행 파일이 없거나 실패해도
복원 성공으로 처리하지 않는다. 기존 소유권·ACL 복원과 --exit-on-error는 유지한다.

현재 호출에서 생성 성공이 확인된 scratch DB만 postgres maintenance DB를 통해
dropdb --if-exists --force한다. 그 뒤 정확한 datname으로 pg_database와
pg_stat_activity의 수를 읽어 둘 다 0인지 확인한다. 다른 DB와 다른 호출의 scratch를
강제 종료하지 않는다. 기존 오래된 scratch 수거의 대상·강제 삭제 여부도 바꾸지 않는다.

DB 삭제·부재 재조회·컨테이너 dump 삭제는 서로 실패해도 모두 시도한다. 각 로컬 명령의
상한은 각각 60·15·30초이며 DB 삭제 프로세스에도 50초+5초 KILL 상한을 둔다.
원 복원 예외는 cause로 유지하고 CLI가 실제 출력하는 오류 문자열에 정리 실패도 붙인다.
복원 exit 0이어도 정리·부재 확인이 실패하면 blocking finding으로 verified를 거부한다.

## 남는 실패 경계

createdb 성공이 확인되지 않으면 그 이름의 DB를 강제 삭제하지 않는다. 생성 실패·응답
유실·기존 이름 충돌을 성공으로 추정해 다른 DB를 지우지 않기 위해서다. 이 경우 정리
미확인을 오류에 명시하고 dump 정리는 시도한다. 생성 응답 유실이 남긴 빈 DB 여부는
운영자가 별도로 확인해야 하며 자동 성공으로 읽지 않는다.

kill -9·호스트/데몬 중단은 finally 실행을 보장하지 못한다. force 삭제는 prepared TX,
logical replication slot 같은 PostgreSQL 제약 때문에 실패할 수도 있다. 이 경우
부재를 주장하지 않고 정리 실패를 남긴다. 원 백업 파일·manifest·실제 role DB의 내용과
권한, 이미지·배포 pin, 다른 복원 실행은 이번 변경의 수정 대상이 아니다.

## 리허설 세션 부하

pg_restore에만 PGOPTIONS=-c max_parallel_maintenance_workers=0을 주어 인덱스 생성이
공용 worker를 추가로 쓰지 않게 한다. PGAPPNAME은 해당 scratch 이름으로 둔다.
전역 설정이나 기존 서비스의 세션은 바꾸지 않는다. 이는 I/O·RSS·전체 CPU 상한이나
실제 복원 시간 단축의 보장이 아니다. 복원 자체는 여전히 공용 PGDATA/WAL을 쓰므로
대형 백업은 낮은 부하 시간대에 돌리고 충분한 유한 --timeout을 선택해야 한다.
현재 운영에서 진행 중인 별도 리허설은 이 소스 변경으로 중단·재설정하지 않았다.

별도로 기존 설치본에서 수행한 운영 관측은 동일 dump의 600초 실행이 부족했고,
명시1800초 재시도는963.094초에 verified true·head409로 끝났다는 결과다. 이는
이 패치의 worker0 설정이 복원을 빠르게 한다는 증거가 아니다. 실제 데이터와 부하에
맞는 유한 timeout 선택의 예이며, 코드의 기존 기본값은 바꾸지 않는다.

## 검증과 후속

새 회귀의 수정 전 8실패 원문을 보존했다. 로컬 mock 회귀는 새15개와 기존 백업 회귀를
합쳐 113개 통과했다. timeout 원인 chain, dump 정리 실패 뒤 DB 정리, DB/backend
부재 확인 실패, 생성 미확인 강제 삭제 금지, session-only 설정을 검증한다.
원 건강한 복원·소유권 이양·head/크기·ACL 지문·디스크 여유 회귀도 유지했다.

첫 소스 checkpoint의 실제 PG 미검증 상태와 원실패는 보존했다. 이후 Root/T 두
독립 소스 리뷰를 통과한 c24d026에서 다음 격리 WSL lab을 실행했다.

## 실제 격리 timeout 검증

고정 cached PostGIS16 이미지 하나를 network none, CPU1, RAM768MiB·추가 swap0,
PID128로 실행했다. 실제 CHECK 함수의 pg_sleep에 걸린 작은 dump를 사용했다.
제품의 full rehearse 호출에서 2초 inner deadline은 exit124로 끝났고, 이 호출은
전체7.740초(내부 리허설5.365초)였다. 별도 docker client 1초 timeout은 실제
TimeoutExpired cause를 남긴 뒤 같은 정리 함수를 실행했다. 두 경우 모두 해당
scratch DB/backend/pg_restore 프로세스가 0이었고, 같은 instance의 다른 own DB에서
돌던 실제 pg_restore는 살아 있었다. 그 별도 복원도 최종 실험 컨테이너 수거로 끝냈다.

소스425파일 전후 내용/OID와 원 dump SHA가 같았다. exact CID·UUID·volume을 모두
정리했고 최종 수거는1.883초였다. 이 실험은 운영의 대형 복원 소요시간·I/O·RSS 상한,
실험 외 DB나 ARM 이미지의 timeout 도구, kill -9/데몬 중단 복구의 수용이 아니다.

앞선 두 fixture 실패도 보존했다. 첫 경우는 geo가 현재 host 소유가 아니라는 정본
guard가 복원 전에 거부했다. 두 번째는 lab만 추가한 /tmp tmpfs의 파일을 Docker cp가
읽지 못했다. host 검사를 유지한 map_application fixture와 운영과 같은 overlay /tmp로
맞춘 뒤 위 결과를 얻었으며, 두 실패를 제품 timeout 경로 실행으로 세지 않는다.
원장 manager556-c24d-owned-timeout-final-facts.json과 그 안의 raw SHA를 별도 보존했다.

제품 c24d026의 백엔드·프론트엔드 CI는 통과했다. 이 문서 정산의 최종 CI와 운영 설치는
후속 단계이며, 운영에서 별도로 완료한 원 큰 복원 결과와 이 격리 검증은 구분한다.
