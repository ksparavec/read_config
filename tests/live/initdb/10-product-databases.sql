-- Foreman, NetBox and AWX each want their own database and role. They share
-- this one PostgreSQL server rather than running three of their own.
CREATE ROLE foreman WITH LOGIN PASSWORD 'foreman';
CREATE DATABASE foreman OWNER foreman;

CREATE ROLE netbox WITH LOGIN PASSWORD 'netbox';
CREATE DATABASE netbox OWNER netbox;

CREATE ROLE awx WITH LOGIN PASSWORD 'awx';
CREATE DATABASE awx OWNER awx;

-- NetBox migrations need extension privileges on their own database.
\connect netbox
CREATE EXTENSION IF NOT EXISTS pg_trgm;
