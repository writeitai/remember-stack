/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { IdentityRegime } from './IdentityRegime';
export type OverlapTemporalScope = {
    believed_at: string;
    evaluated_at: string;
    from: string;
    identity_regime?: IdentityRegime;
    mode?: 'overlap';
    to: string;
};

