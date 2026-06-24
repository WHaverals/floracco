import { Link } from "react-router-dom";

type Props = {
  dbRowId: string;
  sourceEntryId: string;
  onNext: () => void;
  onDismiss: () => void;
};

export default function ReconcileHandoffStrip({ dbRowId, onNext, onDismiss }: Props) {
  return (
    <div className="reconcile-handoff" role="status">
      <div className="reconcile-handoff-text">
        <strong>Link confirmed.</strong> Next: open the record — if the date disagrees with the
        Word source, a “Word source — differs” note on the record shows the evidence.
      </div>
      <div className="reconcile-handoff-actions">
        <Link className="primary-button" to={`/database/${dbRowId.replace(":", "/")}`} onClick={onDismiss}>
          Open in Database →
        </Link>
        <button type="button" className="pill-button" onClick={() => { onDismiss(); onNext(); }}>
          Next case
        </button>
        <button type="button" className="link-button" onClick={onDismiss} aria-label="Dismiss">
          Dismiss
        </button>
      </div>
    </div>
  );
}
