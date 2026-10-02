import {InputValidationError} from './errors';
const queryErrors=new WeakSet<InputValidationError>();
/** Preserve Python SandboxRejection provenance without a second public error class. */
export function queryArgumentError({error}:{error:InputValidationError}):InputValidationError {queryErrors.add(error);return error;}
/** Distinguish open-query validation from ordinary client ValueError adaptations. */
export function isQueryArgumentError({error}:{error:InputValidationError}):boolean {return queryErrors.has(error);}
