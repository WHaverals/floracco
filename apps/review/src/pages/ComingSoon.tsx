import { Link } from "react-router-dom";

export default function ComingSoon({ title, blurb }: { title: string; blurb: string }) {
  return (
    <div className="coming-soon">
      <p className="eyebrow">Planned tool</p>
      <h1>{title}</h1>
      <p className="muted">{blurb}</p>
      <p className="muted">
        This tool is part of the platform plan but not built yet. See{" "}
        <code>docs/review_platform.md</code> for the design.
      </p>
      <Link className="pill-button is-active" to="/">
        Back to hub
      </Link>
    </div>
  );
}
