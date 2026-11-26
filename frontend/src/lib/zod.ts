import { z } from "zod";

/**
 * zod 4 probes `new Function("")` once to decide whether it may compile ("JIT") object parsers. Our CSP has no
 * `'unsafe-eval'`, so the probe is blocked: harmless (zod falls back), but the browser reports a
 * `securitypolicyviolation` and logs a console error. `jitless` skips the probe and uses the interpreted parsers.
 *
 * This runs when the module is first evaluated. The app's schemas are built at module level, and zod reads the
 * flag as each schema is created, so every file that builds a schema imports `z` from here (not from "zod"):
 * that makes the order independent of how the bundler orders chunks.
 */
z.config({ jitless: true });

export { z };
