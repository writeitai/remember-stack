/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PendingRevocation } from './PendingRevocation';
/**
 * Every key still awaiting revocation, oldest first.
 */
export type PendingRevocations = {
    entries?: Array<PendingRevocation>;
    version: 2;
};

