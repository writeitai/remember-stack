/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The claims of a signed key, read **without verifying the signature**.
 *
 * Only for routing and display: the engine perimeter verifies every key.
 */
export type KeyClaims = {
    exp?: number | null;
    iss: string;
    jti?: string | null;
    org?: string | null;
    permissions?: Array<string>;
    projects?: (Array<string> | 'org:*') | null;
    sub?: string | null;
};

