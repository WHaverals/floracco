import { useEffect, useRef } from "react";

/* Layered Escape handling (code-review finding D2). Several overlays used to
 * register independent window `keydown` listeners, so one Escape reached ALL
 * of them at once — closing the manuscript lightbox also killed an open inline
 * editor underneath it (stopPropagation cannot suppress co-registered
 * listeners on the same target). Here a single shared listener dispatches
 * Escape to the TOP layer only: the newest mounted active layer wins
 * (lightbox above picker above editor). */

type Handler = (event: KeyboardEvent) => void;

const stack: { handler: Handler }[] = [];
let listening = false;

function onWindowKeydown(event: KeyboardEvent) {
  if (event.key !== "Escape" || stack.length === 0) return;
  stack[stack.length - 1].handler(event);
}

/** Register `handler` as the top Escape layer while `active` is true. */
export function useEscapeLayer(active: boolean, handler: Handler): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    if (!active) return;
    if (!listening) {
      window.addEventListener("keydown", onWindowKeydown);
      listening = true;
    }
    const entry = { handler: (event: KeyboardEvent) => ref.current(event) };
    stack.push(entry);
    return () => {
      const index = stack.indexOf(entry);
      if (index >= 0) stack.splice(index, 1);
    };
  }, [active]);
}
