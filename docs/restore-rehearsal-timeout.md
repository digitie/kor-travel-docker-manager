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

## 검증과 후속

새 회귀의 수정 전 8실패 원문을 보존했다. 로컬 mock 회귀는 새15개와 기존 백업 회귀를
합쳐 113개 통과했다. timeout 원인 chain, dump 정리 실패 뒤 DB 정리, DB/backend
부재 확인 실패, 생성 미확인 강제 삭제 금지, session-only 설정을 검증한다.
원 건강한 복원·소유권 이양·head/크기·ACL 지문·디스크 여유 회귀도 유지했다.

이 checkpoint는 실제 PostgreSQL timeout 종료 실증이 아니다. 후속 격리 WSL lab에서
짧은 timeout의 실제 복원 프로세스·backend 종료와 scratch 부재, 같은 instance의
다른 DB/복원 생존을 확인한다. 소스 두 독립 리뷰와 lab 실행 범위 확인 전 PG를 새로
기동하지 않는다. 운영 설치는 두 리뷰·CI 이후 별도 배포 단계다.
