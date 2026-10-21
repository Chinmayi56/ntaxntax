-- ============================================================
-- Ledgerloop — Supabase schema
-- Run this in: Supabase Dashboard → SQL Editor → New query
-- ============================================================

-- 1. PROFILES
-- Supabase Auth already creates a row in auth.users on signup.
-- This table extends that with your app-specific fields (role, status, name).
create table if not exists profiles (
  id uuid references auth.users on delete cascade primary key,
  full_name text,
  role text not null default 'staff' check (role in ('owner','staff','client')),
  status text not null default 'pending' check (status in ('pending','active','rejected')),
  email text,
  created_at timestamptz default now()
);

-- Safe to re-run: adds this column if you already created the table
-- before approvals existed. New signups now land as 'pending' by default —
-- they can create an account but can't get into the workspace until a CA
-- (owner) approves them on the Users & Roles page.
alter table profiles add column if not exists status text not null default 'pending';

-- If you already promoted yourself to owner before this update, run this
-- once so your own login isn't stuck pending:
--   update profiles set status = 'active' where role = 'owner';

-- Lets an owner check their own role without RLS recursion (a policy on
-- `profiles` that queries `profiles` directly can deadlock without this).
create or replace function public.is_owner()
returns boolean as $$
  select exists (select 1 from profiles where id = auth.uid() and role = 'owner');
$$ language sql security definer stable;

-- Auto-create a profile row whenever someone signs up
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, email, full_name, role)
  values (new.id, new.email, new.raw_user_meta_data->>'full_name', 'staff');
  return new;
end;
$$ language plpgsql security definer;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();


-- 2. CLIENTS (the CA firm's clients — not the same as portal "client" logins)
create table if not exists clients (
  id uuid default gen_random_uuid() primary key,
  name text not null,
  gstin text,
  type text default 'Business' check (type in ('Business','Practice')),
  contact text,
  phone text,
  status text default 'Onboarding' check (status in ('Onboarding','Active')),
  created_by uuid references profiles(id),
  created_at timestamptz default now()
);

-- Safe to re-run: adds these columns if you already created the table
-- before this update.
alter table clients add column if not exists type text default 'Business';
alter table clients add column if not exists contact text;
alter table clients add column if not exists phone text;
alter table clients add column if not exists status text default 'Onboarding';


-- 3. TASKS — the core "CA assigns work to staff" table
create table if not exists tasks (
  id uuid default gen_random_uuid() primary key,
  title text not null,
  client_id uuid references clients(id),
  assigned_to uuid references profiles(id),   -- <-- this is the assignment
  assigned_by uuid references profiles(id),
  priority text default 'med' check (priority in ('low','med','high')),
  status text default 'todo' check (status in ('todo','progress','done')),
  due_date date,
  created_at timestamptz default now()
);


-- 4. INVOICES
create table if not exists invoices (
  id uuid default gen_random_uuid() primary key,
  invoice_no text not null,
  client_id uuid references clients(id),
  amount numeric(12,2) not null,
  status text default 'pending' check (status in ('pending','paid','overdue')),
  issued_by uuid references profiles(id),
  created_at timestamptz default now()
);


-- ============================================================
-- ROW LEVEL SECURITY — the part that actually enforces "staff
-- only sees their own work", checked by the database itself.
-- ============================================================
alter table profiles enable row level security;
alter table clients  enable row level security;
alter table tasks    enable row level security;
alter table invoices enable row level security;

-- Everyone can read their own profile
drop policy if exists "read own profile" on profiles;
create policy "read own profile" on profiles
  for select using (auth.uid() = id);

-- Owners can read every profile — needed for the Users & Roles page and
-- to review pending approval requests.
drop policy if exists "owners read all profiles" on profiles;
create policy "owners read all profiles" on profiles
  for select using (is_owner());

-- Owners can update any profile — this is what "Approve" / "Reject"
-- actually does: flips a pending row's status to active or rejected.
drop policy if exists "owners update profiles" on profiles;
create policy "owners update profiles" on profiles
  for update using (is_owner());

-- Owners (CAs) can see everything
drop policy if exists "owners see all tasks" on tasks;
create policy "owners see all tasks" on tasks
  for select using (
    exists (select 1 from profiles where id = auth.uid() and role = 'owner')
  );

-- Staff can only see tasks assigned to them
drop policy if exists "staff see own tasks" on tasks;
create policy "staff see own tasks" on tasks
  for select using (assigned_to = auth.uid());

-- Only owners can create/assign tasks
drop policy if exists "owners assign tasks" on tasks;
create policy "owners assign tasks" on tasks
  for insert with check (
    exists (select 1 from profiles where id = auth.uid() and role = 'owner')
  );

-- Staff can update status (e.g. move a task to "done") on their own tasks
drop policy if exists "staff update own task status" on tasks;
create policy "staff update own task status" on tasks
  for update using (assigned_to = auth.uid());

-- Clients/invoices: owners and staff can read; only owners can write
drop policy if exists "team reads clients" on clients;
create policy "team reads clients" on clients for select using (true);

drop policy if exists "owners write clients" on clients;
create policy "owners write clients" on clients for insert with check (
  exists (select 1 from profiles where id = auth.uid() and role = 'owner')
);

drop policy if exists "owners update clients" on clients;
create policy "owners update clients" on clients for update using (
  exists (select 1 from profiles where id = auth.uid() and role = 'owner')
);

drop policy if exists "team reads invoices" on invoices;
create policy "team reads invoices" on invoices for select using (true);

drop policy if exists "owners write invoices" on invoices;
create policy "owners write invoices" on invoices for insert with check (
  exists (select 1 from profiles where id = auth.uid() and role = 'owner')
);