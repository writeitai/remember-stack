BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SET LOCAL lock_timeout='250ms';
SELECT json_build_object(
 'observed_at',clock_timestamp(),'server_version',current_setting('server_version'),
 'schema_head',(SELECT version_num FROM public.alembic_version),
 'extensions',(SELECT json_agg(json_build_object('name',extname,'version',extversion,'schema',n.nspname) ORDER BY extname) FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace),
 'public_physical_table_bytes',(SELECT coalesce(sum(pg_total_relation_size(c.oid)),0) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN('r','m')),
 'relations',(SELECT json_agg(json_build_object(
  'schema',n.nspname,'name',c.relname,'kind',c.relkind,'persistence',c.relpersistence,
  'access_method',a.amname,'partition',c.relispartition,
  'parents',(SELECT json_agg(json_build_object('schema',pn.nspname,'name',p.relname) ORDER BY pn.nspname,p.relname) FROM pg_inherits h JOIN pg_class p ON p.oid=h.inhparent JOIN pg_namespace pn ON pn.oid=p.relnamespace WHERE h.inhrelid=c.oid),
  'extension',(SELECT e.extname FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e' LIMIT 1),
  'index_valid',(SELECT indisvalid FROM pg_index WHERE indexrelid=c.oid),'index_ready',(SELECT indisready FROM pg_index WHERE indexrelid=c.oid),
  'partition_bound',CASE WHEN c.relispartition THEN pg_get_expr(c.relpartbound,c.oid) END,
  'table_owner',(SELECT json_build_object('schema',tn.nspname,'name',t.relname) FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid JOIN pg_namespace tn ON tn.oid=t.relnamespace WHERE x.indexrelid=c.oid),
  'toast',(SELECT json_build_object('schema',tn.nspname,'name',t.relname) FROM pg_class t JOIN pg_namespace tn ON tn.oid=t.relnamespace WHERE t.oid=c.reltoastrelid),
  'main_bytes',pg_relation_size(c.oid,'main'),'fsm_bytes',pg_relation_size(c.oid,'fsm'),'vm_bytes',pg_relation_size(c.oid,'vm'),'init_bytes',pg_relation_size(c.oid,'init'),
  'table_bytes',CASE WHEN c.relkind IN('r','m') THEN pg_table_size(c.oid) END,
  'total_bytes',CASE WHEN c.relkind IN('r','m') THEN pg_total_relation_size(c.oid) END,
  'definition',CASE WHEN c.relkind IN('i','I') THEN pg_get_indexdef(c.oid) WHEN c.relkind IN('v','m') THEN pg_get_viewdef(c.oid,true) END,
  'comment',obj_description(c.oid,'pg_class')
 ) ORDER BY n.nspname,c.relname)
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_am a ON a.oid=c.relam
 WHERE n.nspname NOT IN('pg_catalog','information_schema') AND n.nspname !~ '^pg_temp' AND n.nspname !~ '^pg_toast_temp' AND (n.nspname<>'pg_toast' OR EXISTS (SELECT 1 FROM pg_class owner_c JOIN pg_namespace owner_n ON owner_n.oid=owner_c.relnamespace WHERE owner_n.nspname='public' AND (owner_c.reltoastrelid=c.oid OR owner_c.reltoastrelid IN(SELECT indrelid FROM pg_index WHERE indexrelid=c.oid)))))
)::text;
ROLLBACK;
