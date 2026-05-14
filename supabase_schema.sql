create schema if not exists public;

create table if not exists public.player_balances (
  user_id text primary key,
  balance numeric(12, 2) not null default 250
);

create table if not exists public.processed_command_messages (
  message_id text primary key,
  created_at timestamptz not null default now()
);

grant usage on schema public to anon, authenticated, service_role;
grant all on public.player_balances to service_role;
grant all on public.processed_command_messages to service_role;

notify pgrst, 'reload schema';
