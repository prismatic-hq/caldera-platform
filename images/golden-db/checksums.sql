SELECT format(
  'SELECT %L, encode(sha256(convert_to(coalesce(string_agg(t::text, E''\n'' ORDER BY t::text COLLATE "C"), ''''), ''UTF8'')), ''hex'') FROM %I.%I t',
  schemaname || '.' || tablename, schemaname, tablename
)
FROM pg_tables
WHERE schemaname IN ('tremor', 'steward')
ORDER BY schemaname, tablename
\gexec
