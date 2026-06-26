import { Component, type ErrorInfo, type ReactNode } from "react";

/* A render error anywhere below this boundary shows a recoverable message instead
 * of white-screening the whole app. Resetting on the same view re-mounts the
 * subtree (clearing the error) without a full reload. */
type Props = { children: ReactNode };
type State = { error: Error | null };

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // keep the detail in the console for diagnosis
    console.error("UI error:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="error-boundary">
          <p className="eyebrow">Something went wrong</p>
          <h2>This view hit an error</h2>
          <p className="muted">{this.state.error.message}</p>
          <div className="error-boundary-actions">
            <button type="button" className="pill-button is-primary" onClick={() => this.setState({ error: null })}>
              Try again
            </button>
            <button type="button" className="pill-button" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
