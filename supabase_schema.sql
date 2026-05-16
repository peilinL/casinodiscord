create schema if not exists public;

create table if not exists public.player_balances (
  user_id text primary key,
  balance numeric(12, 2) not null default 250
);

create table if not exists public.processed_command_messages (
  message_id text primary key,
  created_at timestamptz not null default now()
);

create table if not exists public.promo_codes (
  code text primary key,
  amount integer not null check (amount >= 1),
  max_people integer not null check (max_people >= 1),
  created_at timestamptz not null default now()
);

create table if not exists public.promo_redemptions (
  code text not null references public.promo_codes(code) on delete cascade,
  user_id text not null,
  redeemed_at timestamptz not null default now(),
  primary key (code, user_id)
);

grant usage on schema public to anon, authenticated, service_role;
grant all on public.player_balances to service_role;
grant all on public.processed_command_messages to service_role;
grant all on public.promo_codes to service_role;
grant all on public.promo_redemptions to service_role;

notify pgrst, 'reload schema';
