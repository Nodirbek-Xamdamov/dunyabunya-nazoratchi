-- V004 monthly partitions for core.messages and core.transcripts (D-13).
-- Month boundaries are Asia/Tashkent local months (same as the business day, P-07).
-- core.ensure_month_partitions() is idempotent; W0-08 runs it daily as w0_admin (SECURITY DEFINER).
-- Rows that arrived in a DEFAULT partition are moved into the new month partition (never lost).

CREATE TABLE core.partitioned_tables (
    parent   text PRIMARY KEY,
    key_col  text NOT NULL
);
COMMENT ON TABLE core.partitioned_tables IS 'Oylik partitionlanadigan jadvallar ro''yxati (ensure_month_partitions uchun)';
INSERT INTO core.partitioned_tables VALUES ('messages', 'sent_at'), ('transcripts', 'message_sent_at');

CREATE FUNCTION core.month_bounds(p_month date, OUT lo timestamptz, OUT hi timestamptz)
    LANGUAGE sql IMMUTABLE AS $$
    SELECT (date_trunc('month', p_month)::timestamp AT TIME ZONE 'Asia/Tashkent'),
           ((date_trunc('month', p_month) + interval '1 month')::timestamp AT TIME ZONE 'Asia/Tashkent')
$$;

CREATE FUNCTION core.partition_name(p_parent text, p_month date) RETURNS text
    LANGUAGE sql IMMUTABLE AS $$
    SELECT p_parent || '_y' || to_char(p_month, 'YYYY') || 'm' || to_char(p_month, 'MM')
$$;

-- Creates one month partition; moves matching rows out of the DEFAULT partition first.
CREATE FUNCTION core.create_month_partition(p_parent text, p_month date) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, pg_temp AS $$
DECLARE
    key_col text;
    b record;
    part text := core.partition_name(p_parent, p_month);
    cols text;
    moved bigint := 0;
BEGIN
    SELECT t.key_col INTO STRICT key_col FROM core.partitioned_tables t WHERE t.parent = p_parent;
    IF to_regclass('core.' || quote_ident(part)) IS NOT NULL THEN
        RETURN NULL;  -- already there
    END IF;
    SELECT * INTO b FROM core.month_bounds(p_month);
    SELECT string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum) INTO cols
      FROM pg_attribute a
     WHERE a.attrelid = ('core.' || quote_ident(p_parent))::regclass AND a.attnum > 0
       AND NOT a.attisdropped AND a.attgenerated = '';

    DROP TABLE IF EXISTS pg_temp._partition_moved;
    EXECUTE format('CREATE TEMP TABLE _partition_moved AS SELECT %s FROM core.%I WITH NO DATA', cols, p_parent);
    EXECUTE format('WITH d AS (DELETE FROM core.%I WHERE %I >= %L AND %I < %L RETURNING %s) INSERT INTO pg_temp._partition_moved SELECT * FROM d',
                   p_parent || '_default', key_col, b.lo, key_col, b.hi, cols);
    EXECUTE format('CREATE TABLE core.%I PARTITION OF core.%I FOR VALUES FROM (%L) TO (%L)',
                   part, p_parent, b.lo, b.hi);
    EXECUTE format('INSERT INTO core.%I (%s) SELECT %s FROM pg_temp._partition_moved', p_parent, cols, cols);
    GET DIAGNOSTICS moved = ROW_COUNT;
    DROP TABLE pg_temp._partition_moved;
    RETURN moved;
END $$;

CREATE FUNCTION core.ensure_month_partitions(p_months_ahead integer DEFAULT NULL) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, pg_temp AS $$
DECLARE
    ahead integer;
    cur date := date_trunc('month', now() AT TIME ZONE 'Asia/Tashkent')::date;
    t record;
    m date;
    moved bigint;
    created jsonb := '[]';
BEGIN
    ahead := coalesce(p_months_ahead,
                      (SELECT (value #>> '{}')::int FROM core.config WHERE key = 'partitions.months_ahead'), 3);
    FOR t IN SELECT parent, key_col FROM core.partitioned_tables ORDER BY parent LOOP
        FOR m IN
            SELECT (cur + make_interval(months => i))::date FROM generate_series(0, ahead) i
            UNION
            SELECT x.mon FROM core.default_months(t.parent, t.key_col) x
            ORDER BY 1
        LOOP
            moved := core.create_month_partition(t.parent, m);
            IF moved IS NOT NULL THEN
                created := created || jsonb_build_object('partition', core.partition_name(t.parent, m), 'moved_rows', moved);
            END IF;
        END LOOP;
    END LOOP;
    RETURN jsonb_build_object('months_ahead', ahead, 'created', created);
END $$;

CREATE FUNCTION core.default_months(p_parent text, p_key_col text) RETURNS TABLE (mon date)
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = core, pg_temp AS $$
BEGIN
    RETURN QUERY EXECUTE format(
        'SELECT DISTINCT date_trunc(''month'', %I AT TIME ZONE ''Asia/Tashkent'')::date FROM core.%I',
        p_key_col, p_parent || '_default');
END $$;

COMMENT ON FUNCTION core.ensure_month_partitions(integer) IS 'Joriy + keyingi N oy (config partitions.months_ahead) partitionlarini yaratadi va DEFAULT dagi qatorlarni ko''chiradi. Idempotent. W0-08 kunlik chaqiradi';

SELECT core.ensure_month_partitions();
