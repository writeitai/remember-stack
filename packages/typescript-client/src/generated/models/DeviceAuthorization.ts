/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The device authorization response (RFC 8628 §3.2).
 */
export type DeviceAuthorization = {
    device_code: string;
    expires_in: number;
    interval?: number;
    user_code: string;
    verification_uri: string;
    verification_uri_complete?: string | null;
};

