-- Check if llm_insight column exists in recommendations table, if not add it
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='recommendations' AND column_name='llm_insight') THEN
        ALTER TABLE public.recommendations ADD COLUMN llm_insight JSONB;
    END IF;
END
$$;
