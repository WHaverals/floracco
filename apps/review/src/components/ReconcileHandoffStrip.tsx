import { Link } from "react-router-dom";
import { correctionsHandoffUrl } from "../utils/reviewLinks";

type Props = {
  dbRowId: string;
  sourceEntryId: string;
  onNext: () => void;
  onDismiss: () => void;
};

export default function ReconcileHandoffStrip({ dbRowId, sourceEntryId, onNext, onDismiss }: Props) {
  return (
    <div className="reconcile-handoff" role="status">
      <div className="reconcile-handoff-text">
        <strong>Link confirmed.</strong> Next: check whether the date or folio on{" "}
        <code>{dbRowId}</code> should match the Word source.
      </div>
      <div className="reconcile-handoff-actions">
        <Link className="primary-button" to={correctionsHandoffUrl(sourceEntryId, dbRowId)} onClick={onDismiss}>
          Fix in Corrections →
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
