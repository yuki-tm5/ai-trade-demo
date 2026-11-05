-- Review and execute once in a dedicated Supabase FREE project SQL Editor.
create table if not exists public.operations_workspace (
 user_id uuid primary key references auth.users(id),
 version bigint not null default 0 check (version >= 0),
 data jsonb not null,
 updated_at timestamptz not null default now()
);
alter table public.operations_workspace enable row level security;
revoke all on public.operations_workspace from anon;
revoke all on public.operations_workspace from authenticated;
grant select,insert,update on public.operations_workspace to authenticated;
create policy operations_owner_select on public.operations_workspace for select to authenticated using ((select auth.uid())=user_id);
create policy operations_owner_insert on public.operations_workspace for insert to authenticated with check ((select auth.uid())=user_id);
create policy operations_owner_update on public.operations_workspace for update to authenticated using ((select auth.uid())=user_id) with check ((select auth.uid())=user_id);
create function public.operations_workspace_version_guard() returns trigger language plpgsql set search_path=public as $$
begin
 if new.user_id<>old.user_id or new.version<>old.version+1 then raise exception 'invalid workspace update'; end if;
 new.updated_at=now();return new;
end $$;
create trigger operations_workspace_guard before update on public.operations_workspace for each row execute function public.operations_workspace_version_guard();
-- No delete grant, no anonymous read/write, no service_role key in Streamlit.
