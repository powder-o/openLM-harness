import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** e.g. "The map" — used in the fallback's heading. */
  label: string;
  /** Render the fallback as a full pane (Reader/Map) rather than inline in the rail. */
  asPane?: boolean;
  onClose?: () => void;
}

interface State {
  error: Error | null;
}

/** Keeps a crash in one panel from blanking the whole workspace. */
export default class PaneBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`${this.props.label} failed to render`, error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    const note = (
      <div className="crash-note" role="alert">
        <div className="kicker">{this.props.label} couldn't be shown</div>
        <p className="error-text">{this.state.error.message}</p>
        {this.props.onClose && (
          <button type="button" className="btn btn-secondary btn-sm" onClick={this.props.onClose}>
            Close
          </button>
        )}
      </div>
    );
    return this.props.asPane ? <section className="pane">{note}</section> : note;
  }
}
