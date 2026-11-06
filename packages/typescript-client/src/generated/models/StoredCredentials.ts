/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * ``credentials.json`` version 2: one key.
 *
 * - An **issuer key** (``remember login``): ``issuer`` and ``key`` are set,
 * ``api_url`` is not — the engine is resolved from the key.
 * - A **self-hosted entry** (``remember setup --self-hosted``): ``api_url``
 * is set, ``issuer`` is not, and ``key`` is the engine's shared secret (or
 * absent for an engine without authentication).
 *
 * ``extra="forbid"``: this program writes the file, so an unknown field is
 * corruption, and "run ``remember login``" is a recoverable answer.
 */
export type StoredCredentials = {
    api_url?: string | null;
    default_project?: string | null;
    expires_at?: string | null;
    issuer?: string | null;
    key?: string | null;
    key_id?: string | null;
    version: 2;
};

