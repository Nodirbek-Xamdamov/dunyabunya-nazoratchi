DROP VIEW api.v_staff_day;
DROP VIEW api.v_categories;
DROP TABLE core.camera_event_frames;
-- seeded "Hodim N" rows go only if nothing refers to them yet
DELETE FROM core.anon_persons ap
 WHERE ap.note = 'V008 seed (D-31)'
   AND NOT EXISTS (SELECT 1 FROM core.camera_events ce WHERE ce.anon_person_id = ap.id)
   AND NOT EXISTS (SELECT 1 FROM core.presence_sessions ps WHERE ps.anon_person_id = ap.id)
   AND NOT EXISTS (SELECT 1 FROM core.daily_presence dp WHERE dp.anon_person_id = ap.id);
ALTER TABLE core.anon_persons DROP CONSTRAINT anon_persons_staff_permanent;
ALTER TABLE core.anon_persons DROP COLUMN staff_no;
DROP TABLE core.categories;
UPDATE core.config
   SET description = 'P-05 / Q15 (vaqtincha, foydalanuvchi tasdiqlaydi): relevance=no episode matni shuncha soatdan keyin o''chadi'
 WHERE key = 'retention.unrelated_text_hours';
