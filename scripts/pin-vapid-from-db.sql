-- Pin the VAPID keypair to Railway env vars WITHOUT killing your subscription.
--
-- HOW TO RUN IT
--   Railway -> the Postgres service -> Data / Query. Step 1 only, then stop and
--   read it. There is nothing to write here: this script only READS.
--
-- WHY THE ORDER MATTERS, AND WHY YOU MUST NOT GENERATE A NEW PAIR
--
--   A browser push subscription is cryptographically bound to the public key it
--   was created with. You registered one today -- push/status shows
--   subscriptions: 1, and the 07:00 digest has fired on 08-30 and 08-31.
--
--   Pin a NEWLY GENERATED keypair and that subscription is dead the moment the
--   deploy restarts. It does not fail loudly: the send is rejected at the push
--   gateway, so the app records a successful fan-out to nobody and your phone
--   simply stops buzzing. The app prunes on 403 (push_service.DEAD_CODES),
--   which cleans up the corpse but does not tell you why it died.
--
--   So: pin the pair THIS DATABASE ALREADY MINTED. That is what step 1 reads.
--   After PR 30, get_vapid() compares the two and logs VAPID MISMATCH at ERROR
--   if they differ, and push/status carries vapid_env_matches_db -- but the
--   cheapest place to get this right is here, before setting anything.
--
-- WHAT PINNING ACTUALLY BUYS
--   Today the pair lives in kv_settings, so it survives restarts and redeploys.
--   It does NOT survive the database being reset or restored from an older
--   backup -- and on that day every subscription dies silently and every device
--   has to re-subscribe with no prompt to do so. Pinning to env vars makes the
--   keypair independent of the database's lifetime. That is the whole benefit:
--   insurance against one specific, quiet, hard-to-diagnose failure.


-- ===========================================================================
-- STEP 1 - READ THE PAIR. This is the only step that touches the database.
-- ===========================================================================
-- Expect exactly two rows. If you get ZERO, no keypair has been minted yet and
-- there is nothing to preserve -- in that case generating a fresh pair is safe,
-- because there is no subscription bound to anything.
--
-- TREAT THE PRIVATE KEY THE WAY YOU NOW TREAT THE AGENT KEY: it goes from this
-- console straight into the Railway variable. Not into a file, not into a
-- screenshot, not into a chat window. Anyone holding it can send push
-- notifications that your app's users will trust as coming from you.
SELECT key, value
FROM   kv_settings
WHERE  key IN ('vapid_public_key', 'vapid_private_key')
ORDER  BY key;


-- ===========================================================================
-- STEP 2 - SET THEM ON RAILWAY.   (no SQL; do this in the dashboard)
-- ===========================================================================
--   VAPID_PUBLIC_KEY   = the vapid_public_key  value from step 1, verbatim
--   VAPID_PRIVATE_KEY  = the vapid_private_key value from step 1, verbatim
--
-- Verbatim means verbatim: these are base64url with no padding, so a trailing
-- space or a truncated copy produces a key that is well-formed and wrong.
-- Then redeploy.


-- ===========================================================================
-- STEP 3 - VERIFY, from outside the database.
-- ===========================================================================
--   GET /api/v1/push/status
--     vapid_pinned_by_env    should flip  false -> true
--     vapid_env_matches_db   should be    true          <- the one that matters
--     subscriptions          should still be 1
--
-- If vapid_env_matches_db comes back FALSE, you pinned a different pair than the
-- database holds. Fix the two variables from step 1 rather than leaving it: the
-- existing subscription cannot be reached until they match, and the failure is
-- invisible from the phone's side.
--
-- Then confirm with your own eyes: the 07:00 digest should still arrive
-- tomorrow. That is the only end-to-end proof, and no HTTP call substitutes for
-- it.
SELECT key, left(value, 12) || '...' AS starts_with, length(value) AS len
FROM   kv_settings
WHERE  key IN ('vapid_public_key', 'vapid_private_key')
ORDER  BY key;
