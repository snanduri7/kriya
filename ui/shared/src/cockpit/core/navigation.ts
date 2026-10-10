import { createContext, useContext } from 'react';

/**
 * The shell's one generic navigation affordance: go to a page by its id (the registry's page ids). Modules call it with the id of a page they know
 * exists by its placement contract (never by importing the module that provides it); a shell without a page of that id simply ignores the request.
 */
export type Navigate = (pageId: string) => void;
export const NavigationContext = createContext<Navigate>(() => undefined);
export const useNavigate = (): Navigate => useContext(NavigationContext);
