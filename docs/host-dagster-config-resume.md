# 호스트별 공용 Dagster 구성 재개

- 기준 main6dd의 별도 Linux 작업트리에서 설계만 고정했다. 원558 작업트리와 원본 dirty 저장소를 보존한다.
- 현재 범위는 n1506/Odroid5 보존이며 [설계와 미완 과제](host-dagster-instance-config.md)를 따른다. 세부 path/공식 외부 consumer 접점은 Root 설계 검토 후 구현한다.
- 신규 SSH/PG/Docker/빌드/설치/운영 변경0. CI·두 독립 리뷰·실제 적용은 아직 미완이다.
