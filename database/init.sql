-- Run as the PostgreSQL administrator when creating a dedicated DocDesk database.
\set ON_ERROR_STOP on
\getenv app_password DOCDESK_DB_PASSWORD

CREATE ROLE docdesk LOGIN PASSWORD :'app_password';
CREATE SCHEMA docdesk AUTHORIZATION docdesk;
ALTER ROLE docdesk SET search_path TO docdesk;
GRANT CONNECT ON DATABASE docdesk TO docdesk;
