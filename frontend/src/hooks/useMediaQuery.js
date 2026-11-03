import { useEffect, useState } from 'react';

/** Returns true if viewport is desktop width (>= 1024px). Defaults to false in SSR/jsdom. */
export function useIsDesktop() {
  const [isDesktop, setIsDesktop] = useState(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return false;
    }
    try {
      return window.matchMedia('(min-width: 1024px)').matches;
    } catch {
      return false;
    }
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }
    try {
      const mql = window.matchMedia('(min-width: 1024px)');
      const handler = (event) => setIsDesktop(event.matches);
      if (mql.addEventListener) {
        mql.addEventListener('change', handler);
        return () => mql.removeEventListener('change', handler);
      }
      mql.addListener(handler);
      return () => mql.removeListener(handler);
    } catch {
      return undefined;
    }
  }, []);

  return isDesktop;
}
