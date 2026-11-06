/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The document a supplied reference points at, named by its source identity.
 *
 * ``source_kind``/``source_ref`` are the target lineage's identity (the
 * values it was or will be ingested with), so a reference may name a
 * document that is not ingested yet. ``version_key`` pins one version
 * (with ``binding = pinned``); ``section_key`` names a section, else the
 * whole document.
 */
export type OutputReferenceTarget = {
    section_key: string | null;
    source_kind: string;
    source_ref: string;
    version_key: string | null;
};

