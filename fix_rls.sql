-- Disable RLS for all tables (the "Force" way)
ALTER TABLE public.users DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_settings DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.soil_readings DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.disease_predictions DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.recommendations DISABLE ROW LEVEL SECURITY;

-- As a backup, add "Allow All" policies in case RLS stays enabled
-- Users
DROP POLICY IF EXISTS "Allow all users" ON public.users;
CREATE POLICY "Allow all users" ON public.users FOR ALL USING (true) WITH CHECK (true);

-- User Settings
DROP POLICY IF EXISTS "Allow all settings" ON public.user_settings;
CREATE POLICY "Allow all settings" ON public.user_settings FOR ALL USING (true) WITH CHECK (true);

-- Soil Readings
DROP POLICY IF EXISTS "Allow all readings" ON public.soil_readings;
CREATE POLICY "Allow all readings" ON public.soil_readings FOR ALL USING (true) WITH CHECK (true);

-- Disease Predictions
DROP POLICY IF EXISTS "Allow all predictions" ON public.disease_predictions;
CREATE POLICY "Allow all predictions" ON public.disease_predictions FOR ALL USING (true) WITH CHECK (true);

-- Recommendations
DROP POLICY IF EXISTS "Allow all recommendations" ON public.recommendations;
CREATE POLICY "Allow all recommendations" ON public.recommendations FOR ALL USING (true) WITH CHECK (true);
