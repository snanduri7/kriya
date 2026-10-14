import { createContext, useContext } from 'react';

/**
 * The shell's one generic navigation affordance: go to a page by its id (the registry's page ids). Modules call it with the id of a page they know
 * exists by its placement contract (never by importing the module that provides it); a shell without a page of that id simply ignores the request.
 */
export type Navigate = (pageId: string) => void;
export const NavigationContext = createContext<Navigate>(() => undefined);
export const useNavigate = (): Navigate => useContext(NavigationContext);

/** The page that shows the selected run's documents (the registry's page id for run-scoped modules): where a review of a live run's change and validation is read. */
export const RUN_DOCUMENTS_PAGE = 'runs';

/** The page the person works from: the open folder's Project Home (goal entry, the current run, recent runs). Shell and modules navigate to it by this id. */
export const PROJECT_PAGE = 'project';
