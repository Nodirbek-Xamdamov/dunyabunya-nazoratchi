-- V011 W3-03 [CONTRACT]: the only way a human answers review_queue. One transaction: lock the open row, write the
-- answer, append audit_log. Once this function exists roles.py stops granting W3 direct UPDATE on review_queue and
-- INSERT on audit_log (W3 keeps SELECT on review_queue). Until W3-05 the dashboard passes reviewed_by = 'dashboard-local'.

CREATE FUNCTION core.review_decide(p_review_id bigint, p_decision text, p_reviewed_by text, p_details jsonb DEFAULT '{}')
    RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, pg_temp AS $$
DECLARE
    r        core.review_queue%ROWTYPE;
    who      text := btrim(coalesce(p_reviewed_by, ''));
    extra    jsonb := coalesce(p_details, '{}'::jsonb);
    at_      timestamptz := now();
    audit_id bigint;
BEGIN
    IF p_decision IS NULL OR p_decision NOT IN ('confirm', 'reject', 'correct') THEN
        RAISE EXCEPTION 'review_decide: decision must be confirm | reject | correct, got %', coalesce(p_decision, 'NULL')
            USING ERRCODE = '22023';
    END IF;
    IF who = '' OR length(who) > 200 THEN
        RAISE EXCEPTION 'review_decide: reviewed_by must be 1..200 characters' USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(extra) <> 'object' THEN
        RAISE EXCEPTION 'review_decide: details must be a JSON object' USING ERRCODE = '22023';
    END IF;
    IF p_decision = 'correct' AND (extra - 'note') = '{}'::jsonb THEN
        RAISE EXCEPTION 'review_decide: correct needs the corrected values in details (e.g. categories, sentiment, relevance)'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO r FROM core.review_queue WHERE id = p_review_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'review_decide: review % not found', p_review_id USING ERRCODE = 'P0002';
    END IF;
    IF r.status <> 'open' THEN
        RAISE EXCEPTION 'review_decide: review % is already resolved (% by % at %)',
            p_review_id, r.human_decision, r.reviewed_by, r.reviewed_at USING ERRCODE = '55000';
    END IF;

    UPDATE core.review_queue
       SET human_decision = p_decision, reviewed_by = who, reviewed_at = at_,
           details = CASE WHEN p_decision = 'correct' THEN details || jsonb_build_object('human_correction', extra)
                          ELSE details END
     WHERE id = p_review_id;

    INSERT INTO core.audit_log (at, actor, action, object_type, object_id, details, client_addr)
    VALUES (at_, who, 'review_decide', 'review_queue', p_review_id::text,
            jsonb_build_object('decision', p_decision, 'item_type', r.item_type, 'item_id', r.item_id,
                               'reason', r.reason, 'details', extra, 'db_user', session_user,
                               'previous', jsonb_build_object('human_decision', r.human_decision,
                                                              'reviewed_by', r.reviewed_by, 'reviewed_at', r.reviewed_at)),
            inet_client_addr())
    RETURNING id INTO audit_id;

    RETURN jsonb_build_object('review_id', p_review_id, 'decision', p_decision, 'reviewed_by', who,
                              'reviewed_at', at_, 'audit_id', audit_id);
END
$$;
COMMENT ON FUNCTION core.review_decide(bigint, text, text, jsonb) IS 'W3-03: odam qarori (confirm | reject | correct) faqat ochiq review_queue qatoriga; shu tranzaksiyada audit_log. correct: tuzatilgan qiymatlar details da (review_queue.details.human_correction ga ham yoziladi). reviewed_by vaqtincha dashboard-local (W3-05 gacha)';
