/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { IdentityRegime } from './IdentityRegime';
export type AsOfTemporalScope = {
    believed_at: string;
    evaluated_at: string;
    identity_regime?: IdentityRegime;
    mode?: 'as_of';
    valid_at: string;
};

