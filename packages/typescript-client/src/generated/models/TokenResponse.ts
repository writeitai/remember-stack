/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The token response (RFC 6749 §5.1) plus the issuer's non-secret extras.
 */
export type TokenResponse = {
    access_token: string;
    default_project?: string | null;
    expires_in?: number | null;
    key_id?: string | null;
    token_type: string;
};

