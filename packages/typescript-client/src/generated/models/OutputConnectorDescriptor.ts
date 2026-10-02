/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputJsonValue } from './OutputJsonValue';
export type OutputConnectorDescriptor = {
    configuration: Record<string, OutputJsonValue>;
    connector_id: string;
    credential_ref: string | null;
    kind: string;
    message: string | null;
    name: string;
    status: 'active' | 'paused' | 'error';
};

