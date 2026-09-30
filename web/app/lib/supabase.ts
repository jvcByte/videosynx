"use client";

import { SupabaseClient, createClient } from "@supabase/supabase-js";

/**
 * The Supabase client, or null when this build has no project configured.
 *
 * Null is a supported state, not a failure: with no project the app behaves
 * exactly as it always has, sending the development header and showing no
 * sign-in screen. That is what keeps `npm run dev` working without a Supabase
 * account and keeps the browser tests running against the same code path they
 * have always used.
 *
 * Both values are public by design -- the anon key is meant to be shipped to
 * browsers and is constrained by row-level security at the database, not by
 * being secret. So inlining them at build time is right here, unlike
 * PYCODING_API_URL, which is why that one is a server-side rewrite instead.
 */
const URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const supabase: SupabaseClient | null =
  URL && ANON_KEY
    ? createClient(URL, ANON_KEY, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          // The token arrives in the URL fragment on some flows, and reading
          // it is what completes a magic-link or email-confirmation sign-in.
          detectSessionInUrl: true,
        },
      })
    : null;

/** Whether this build signs users in at all. */
export const authEnabled = supabase !== null;
