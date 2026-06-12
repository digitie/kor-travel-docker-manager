-- kraddr_geo, tripmate, krtour_map, kor_travel_concierge 데이터베이스 및 사용자 통합 생성

CREATE DATABASE tripmate;
CREATE DATABASE krtour_map;
CREATE DATABASE kor_travel_concierge;

-- tripmate 사용자 생성 및 권한 부여
CREATE USER tripmate WITH PASSWORD 'tripmate_dev_password';
GRANT ALL PRIVILEGES ON DATABASE tripmate TO tripmate;

-- krtour_map 사용자 생성 및 권한 부여
CREATE USER krtour_map WITH PASSWORD 'krtour_map_dev_password';
GRANT ALL PRIVILEGES ON DATABASE krtour_map TO krtour_map;

-- 1. kor_travel_concierge 데이터베이스 초기화 및 PostGIS 확장 설정
\c kor_travel_concierge
CREATE EXTENSION IF NOT EXISTS postgis;
GRANT ALL PRIVILEGES ON SCHEMA public TO addr;

-- 2. krtour_map 데이터베이스 초기화 및 PostGIS 확장 설정
\c krtour_map
CREATE SCHEMA IF NOT EXISTS feature;
CREATE SCHEMA IF NOT EXISTS provider_sync;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS x_extension;

CREATE EXTENSION IF NOT EXISTS postgis           SCHEMA x_extension;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS pg_trgm           SCHEMA x_extension;
CREATE EXTENSION IF NOT EXISTS pgcrypto          SCHEMA x_extension;

ALTER DATABASE krtour_map SET search_path = public, x_extension;

GRANT ALL PRIVILEGES ON SCHEMA public TO krtour_map;
GRANT ALL PRIVILEGES ON SCHEMA feature TO krtour_map;
GRANT ALL PRIVILEGES ON SCHEMA provider_sync TO krtour_map;
GRANT ALL PRIVILEGES ON SCHEMA ops TO krtour_map;
GRANT ALL PRIVILEGES ON SCHEMA x_extension TO krtour_map;

-- 3. tripmate 데이터베이스 초기화 및 PostGIS 확장 설정
\c tripmate
CREATE EXTENSION IF NOT EXISTS postgis;
GRANT ALL PRIVILEGES ON SCHEMA public TO tripmate;
