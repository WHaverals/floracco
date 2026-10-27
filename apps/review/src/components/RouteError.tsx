import { useRouteError } from "react-router-dom";

/** Shown when something outside a page's own error box fails (the top bar, the
 * app shell). Plain words instead of the router's default screen with its stack
 * trace; the detail stays on hover and in the console. */
export default function RouteError() {
  const error = useRouteError();
  const detail = error instanceof Error ? error.message : String(error ?? "");
  console.error("UI error:", error);
  return (
    <div className="error-boundary">
      <p className="eyebrow">Something went wrong</p>
      <h2 title={detail}>This page could not be shown</h2>
      <div className="error-boundary-actions">
        <button type="button" className="pill-button is-primary" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    </div>
  );
}
