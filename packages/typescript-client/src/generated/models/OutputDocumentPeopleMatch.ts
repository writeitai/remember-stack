/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One distinct person an authors/recipients filter matched.
 */
export type OutputDocumentPeopleMatch = {
    address: string | null;
    documents: number;
    name: string | null;
    role: 'author' | 'recipient';
};

