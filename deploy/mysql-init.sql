-- 只读账号：供 sql_query 工具使用（仅本库 SELECT）。
-- 与本地开发时手工创建的 agentforge_ro 保持一致，使容器内的 SQL 安全边界不打折。
CREATE USER IF NOT EXISTS 'agentforge_ro'@'%' IDENTIFIED BY 'agentforge_ro_2026';
GRANT SELECT ON agentforge.* TO 'agentforge_ro'@'%';
FLUSH PRIVILEGES;
