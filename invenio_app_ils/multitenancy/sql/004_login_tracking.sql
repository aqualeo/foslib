-- SPDX-License-Identifier: MIT
-- Invenio's relationship-based login tracking also advances the parent account
-- optimistic-lock counter. Do not grant updates to passwords, profiles, roles,
-- activation/verification flags or other authentication identity fields.
GRANT UPDATE(version_id,updated) ON public.accounts_user TO foslib_runtime;
