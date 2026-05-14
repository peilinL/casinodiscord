create table if not exists public.player_balances (
  user_id text primary key,
  balance numeric(12, 2) not null default 250
);
