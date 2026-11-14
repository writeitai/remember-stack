# D155 live corpus capture classification

D152 capture still expanded retired immutable projection manifests after D155
replaced that writer with a mutable document tree. A folder's presence in
`documents.corpus_paths` proves only that publication might have written it:
the set is committed before IO and retained across failed moves and cleanup.
Treating every recorded prefix as published would count incomplete copies.

The existing served-reading selector supplies all DB inputs to
`desired_export_revision`. Read those inputs and every document's recorded paths
through the original bounded native capture view. Require the current marker
to match that revision and the recorded set to contain exactly the desired
folder. Expand its known reader files from the hash-bound immutable conversion
inventory. Do not read mutable corpus `document.json` as publication authority.
Missing required files refuse completion. Other files beneath a valid D155
suffix folder are staging, including unrecorded paths, deleted/forgotten
documents and interrupted publication. Unknown keys outside the writer grammar
refuse capture instead of inheriting an inclusion rule.

Retain D152's purpose/component vocabulary: separate native classes identify
corpus original copies, derivatives and navigation, all `projection_origin` in
`p3_origin`. Raw originals remain `canonical_raw` in `canonical_object`. Thus
equal payloads at different physical keys remain different resources and
corpus copies never inflate canonical-original quantities. Shared navigation
uses the current type/topic/radix grammar; an incomplete document's own index
is staging with the rest of that folder.

Alternatives rejected: snapshot-manifest support would preserve a second
classifier without a writer; prefix-only attribution would bill partial writes;
trusting a non-null marker without recomputing its input identity would accept
stale metadata or policy; introducing a publication table or counter duplicates
D155's existing inputs and marker. D155's accepted late-request repair race
remains: the marker proves a successful publication of these inputs, not an
atomic or byte-immutable tree. Provider evidence owns existence, version and
quantity at the requested cut; current listings supply private names only.

`projection_snapshots` is still read by `OperationalCatalog.inspect` for the
historical `/operations.latest_projections` field. Retain that relation and its
PostgreSQL allocation rule; remove it from the capture reader's extraction
locks and locator reads. No migration or API export change is needed. Pin both
native capture helpers to the actual `p9_52_0073` head, without an old-head
fallback. Generated regression profiles are synthetic inputs, never operator
clock, provider, image or cost qualification. No paid-work identity changes.
