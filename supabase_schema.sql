-- 1. Create Users Table in 'public' schema
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    google_id TEXT UNIQUE NOT NULL, -- This will store the Supabase Auth UID
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    avatar_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Create User Settings Table
CREATE TABLE IF NOT EXISTS user_settings (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    th_sm_min FLOAT DEFAULT 30.0,
    th_sm_max FLOAT DEFAULT 80.0,
    th_temp_min FLOAT DEFAULT 10.0,
    th_temp_max FLOAT DEFAULT 35.0,
    th_hum_min FLOAT DEFAULT 40.0,
    th_hum_max FLOAT DEFAULT 80.0,
    cal_sm FLOAT DEFAULT 0.0,
    cal_temp FLOAT DEFAULT 0.0,
    cal_hum FLOAT DEFAULT 0.0,
    city TEXT DEFAULT 'Nabadwip',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Create Soil Readings Table
CREATE TABLE IF NOT EXISTS soil_readings (
    id BIGSERIAL PRIMARY KEY,
    device_id TEXT,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    temperature FLOAT,
    humidity FLOAT,
    soil_moisture FLOAT,
    soil_dry BOOLEAN,
    soil_wet BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Create Disease Predictions Table
CREATE TABLE IF NOT EXISTS disease_predictions (
    id BIGSERIAL PRIMARY KEY,
    device_id TEXT,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    disease TEXT,
    crop TEXT,
    confidence FLOAT,
    severity TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- 5. AUTOMATIC USER PROFILE TRIGGER (The "Supabase Way")
-- This function runs every time a new user signs up in Supabase Auth
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.users (google_id, email, name, avatar_url)
  VALUES (
    new.id::text, 
    new.email, 
    new.raw_user_meta_data->>'full_name', 
    new.raw_user_meta_data->>'avatar_url'
  );
  
  -- Also initialize settings for the new user
  INSERT INTO public.user_settings (user_id)
  SELECT id FROM public.users WHERE google_id = new.id::text;
  
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger the function after a new user is created in 'auth.users'
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- 6. RLS (Row Level Security) - Minimal for development
-- Disable RLS for now to allow your Flask app to work with the publishable key
ALTER TABLE public.users DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_settings DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.soil_readings DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.disease_predictions DISABLE ROW LEVEL SECURITY;

-- 7. Device Mapping Table (New)
-- Maps a physical device_id to a specific user_id
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Policy to allow all for devices (since we're in dev mode)
ALTER TABLE public.devices DISABLE ROW LEVEL SECURITY;

-- 7. Device Mapping Table (New)
-- Maps a physical device_id to a specific user_id
CREATE TABLE IF NOT EXISTS public.devices (
    device_id TEXT PRIMARY KEY,
    user_id BIGINT REFERENCES public.users(id) ON DELETE CASCADE,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Policy to allow all for devices (since we're in dev mode)
ALTER TABLE public.devices DISABLE ROW LEVEL SECURITY;
