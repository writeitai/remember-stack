# Storage resource authority: reachable growth and funding

**Status: non-binding analysis in progress.** This examines the managed
integration alongside the complete engine proposal; it chooses no price,
waiver, new customer cap or changed billing quantity. Provider D63 was accepted
at fsit `8b71c600` without implementation/live proof. Its filesystem-ID source
correction at `666b9e1a` remains an integration dependency pending review/merge:
filesystem IDs are native canonical 26-character lowercase unpadded base32,
not UUID32. Organisation/engine UUIDs keep their own type.

Source findings already narrow the problem:

- UMC multipart start takes then releases a spend lease **before** creating
  the upload and returning six-hour, signed-length part URLs. This checks
  current request admission, not a durable storage-growth hold for the URLs'
  remaining lifetime. Completion takes another request lease; the engine's
  processed-input/storage bundle occurs later in conversion.
- Completed unregistered objects have a 24-hour age threshold, but cleanup
  resolves an active Project context and skips busy keys; blocked Projects
  wait for reactivation or filesystem deletion. The threshold is not a hard
  physical-retention bound. Provider multipart expiration/aborts also retain
  unknown outcomes and retry cleanup.
- The current UMC text storage formula bounds a processed-input measurement;
  its inspected v4 seed still names engine `>=0.13.0,<0.14.0`, while the
  recorded runtime pins 0.19.0. It is not qualification for every current
  pre-upload/card/K/P3/staging mutation. Processing failure currently releases
  both processing and storage-growth holds independently of retained raw data.
- Provider quota reservation is real and atomic over retained plus pending
  bytes, with optional supplier spending caps. It is not a UMC customer
  financial hold, a per-mutation lifetime bound or proof of physical garbage
  reclamation. Runtime provisioning requests only a name and accepts provider
  defaults; a source default is not an observed serving limit.

The completed analysis will enumerate each reachable disposition, the exact
existing enforcement and missing bounds, then define typed profile evidence
and pre-enable gates. No live calls or source/runtime changes are made here.
