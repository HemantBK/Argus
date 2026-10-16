import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
}

/** Last line of defence: a render error is shown on screen, never a blank page. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Argus render error", error, info.componentStack);
  }

  override render(): ReactNode {
    if (this.state.error) return <ErrorBanner title="Argus hit an error and cannot show this page." error={this.state.error} />;
    return this.props.children;
  }
}

/** A plain-English error for the user: the message only, never a stack, error class name or raw log. */
export function ErrorBanner({ title, error }: { title: string; error: Error }): ReactNode {
  return (
    <div className="error-banner" role="alert">
      <strong>{title}</strong>
      <p className="error-detail">{error.message}</p>
    </div>
  );
}
