-- kraddr_geo 및 tripmate_agent 데이터베이스 초기 확장 설정
CREATE DATABASE tripmate_agent;

-- 권한 설정
GRANT ALL PRIVILEGES ON DATABASE tripmate_agent TO addr;

-- PostGIS 확장 설정
\c tripmate_agent
CREATE EXTENSION IF NOT EXISTS postgis;
