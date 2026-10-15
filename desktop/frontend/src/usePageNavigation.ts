import { useEffect, useRef, useState } from 'react';
type Page = 'settings' | 'character' | null;
const fromHash = (): Page =>
  location.hash === '#/settings'
    ? 'settings'
    : location.hash === '#/character'
      ? 'character'
      : null;
/** Page changes leave the mounted Chat/Work session and background task intact. */
export function usePageNavigation(base: string) {
  const [page, setState] = useState<Page>(fromHash),
    origin = useRef('#/' + base);
  useEffect(() => {
    const pop = () => setState(fromHash());
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, []);
  useEffect(() => {
    if (!page) {
      origin.current = '#/' + base;
      history.replaceState(null, '', origin.current);
    }
  }, [base, page]);
  function setPage(next: Page) {
    if (next) {
      if (!page) origin.current = location.hash || '#/' + base;
      history.pushState(null, '', '#/' + next);
    } else history.replaceState(null, '', origin.current);
    setState(next);
  }
  return { page, setPage };
}
