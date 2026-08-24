import { useCallback, useRef } from "react";

/** Guard against out-of-order async responses (the stale-response class,
 * code-review finding D1): the app must never believe an answer to an
 * outdated question just because it arrived last.
 *
 * One instance per logical stream (a record load, a search, a flags refresh):
 *
 *   const freshSearch = useLatest();
 *   ...
 *   const fresh = freshSearch();          // this request is now the newest
 *   api(...).then((r) => { if (fresh()) setState(r); });
 *
 * A newer begin() invalidates every older request's check, so a slow earlier
 * response can no longer overwrite a faster later one — and a cleared input
 * can kill an in-flight request by just calling begin() again. */
export function useLatest(): () => () => boolean {
  const seq = useRef(0);
  return useCallback(() => {
    const id = ++seq.current;
    return () => id === seq.current;
  }, []);
}
