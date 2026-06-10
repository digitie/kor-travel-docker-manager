-- krtour_map 데이터베이스 생성
CREATE DATABASE krtour_map;

-- krtour_map 유저 생성 및 권한 부여
CREATE USER krtour_map WITH PASSWORD 'krtour_map_dev_password';
GRANT ALL PRIVILEGES ON DATABASE krtour_map TO krtour_map;

-- krtour_map 데이터베이스에 연결하여 스키마 및 PostGIS 확장 구성
\c krtour_map

CREATE SCHEMA IF NOT EXISTS feature;
CREATE SCHEMA IF NOT EXISTS provider_sync;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS x_extension;

CREATE EXTENSION IF NOT EXISTS postgis           SCHEMA x_extension;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS pg_trgm           SCHEMA x_extension;
CREATE EXTENSION IF NOT EXISTS pgcrypto          SCHEMA x_extension;

-- search_path에 x_extension 추가
ALTER DATABASE krtour_map SET search_path = public, x_extension;

-- 스키마 권한 부여
GRANT ALL PRIVILEGES ON SCHEMA public TO krtour_map;
GRANT ALL PRIVILEGES ON SCHEMA feature TO krtour_map;
GRANT ALL PRIVILEGES ON SCHEMA provider_sync TO krtour_map;
GRANT ALL PRIVILEGES ON SCHEMA ops TO krtour_map;
GRANT ALL PRIVILEGES ON SCHEMA x_extension TO krtour_map;
