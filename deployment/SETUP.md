# Persistent tester workspace setup (manual authorization required)

1. Create a dedicated Supabase FREE project. No paid tier required. Do not use a production business database.
2. In SQL Editor, review and run deployment/supabase_workspace.sql ONCE. It creates owner-scoped rows and denies anonymous access. No service_role key belongs in this app.
3. In Authentication > Users create at least TWO invited test users with distinct email/passwords. Do not share one login across testers. Disable public signup if unnecessary. No signup or SMTP setup is required in this Demo.
4. Streamlit Community Cloud > this app > Settings > Secrets:

```toml
SUPABASE_URL = "https://tepewzoushuyvnipwqtq.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "sb_publishable_YOUR_PROJECT_KEY"
```

Copy the new public publishable project key from Supabase API settings. NEVER enter a database password, service_role/secret key or QQ authorization code. Do not send keys or test passwords in chat. Reboot the Streamlit app after saving Secrets.
5. Log in with tester A. Confirm a suggestion, add a task, save notes, complete it; refresh/reboot and sign out/in: all state/history must survive. Log in with B: A's task must not appear. Try update/select through B's authenticated API: owner A must not be accessible. Test simultaneous stale-version save: must conflict, not overwrite.
6. Click simulated-new-mail (demo-only, per-user). Dates must retain both old/new evidence; done tasks remain done. Undo a task action must NOT remove new email evidence or mark a business event complete.

Until the two-user live database tests and reboot persistence test are actually passed, do not declare persistence/isolation verified or invite business users for task-writing acceptance. Missing configuration disables every write and simulation update; it never falls back to session_state or Streamlit disk. Tokens are transient session auth only; task state is in Supabase with optimistic version checks. Expired auth fails closed and requires re-login; refresh-token automation is not yet implemented.

New configuration priority: SUPABASE_PUBLISHABLE_KEY (Streamlit Secrets, then environment) before legacy SUPABASE_ANON_KEY fallback. Remove the old name after migration. No supabase Python SDK is installed: requests calls Supabase Auth/PostgREST directly. Public keys go only in apikey; a signed-in user access JWT alone goes in Authorization. Both sb_secret_ and legacy service_role configuration are rejected.
