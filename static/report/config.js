// Overwritten at build time by build.py from SUPABASE_URL and
// SUPABASE_PUBLISHABLE_KEY. The checked-in version is empty on purpose: the
// deployed values belong to the environment, not to git.
//
// The publishable key is designed to be public — it is subject to Row Level
// Security, and every policy in 0001/0002 grants SELECT on football data only.
// The SECRET key must never appear in this file or anywhere else under
// static/.
export const SUPABASE_URL = "";
export const SUPABASE_PUBLISHABLE_KEY = "";
