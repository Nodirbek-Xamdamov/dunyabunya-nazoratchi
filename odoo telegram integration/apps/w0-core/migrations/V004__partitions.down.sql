-- Move every month partition's rows back into the DEFAULT partition, then drop the partitions.
DO $$
DECLARE
    t record;
    p record;
    cols text;
BEGIN
    FOR t IN SELECT parent FROM core.partitioned_tables LOOP
        SELECT string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum) INTO cols
          FROM pg_attribute a
         WHERE a.attrelid = ('core.' || quote_ident(t.parent))::regclass AND a.attnum > 0
           AND NOT a.attisdropped AND a.attgenerated = '';
        FOR p IN
            SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
             WHERE i.inhparent = ('core.' || quote_ident(t.parent))::regclass
               AND c.relname <> t.parent || '_default'
        LOOP
            EXECUTE format('ALTER TABLE core.%I DETACH PARTITION core.%I', t.parent, p.relname);
            EXECUTE format('INSERT INTO core.%I (%s) SELECT %s FROM core.%I', t.parent, cols, cols, p.relname);
            EXECUTE format('DROP TABLE core.%I', p.relname);
        END LOOP;
    END LOOP;
END $$;
DROP FUNCTION core.ensure_month_partitions(integer);
DROP FUNCTION core.default_months(text, text);
DROP FUNCTION core.create_month_partition(text, date);
DROP FUNCTION core.partition_name(text, date);
DROP FUNCTION core.month_bounds(date);
DROP TABLE core.partitioned_tables;
