import { Link } from "react-router-dom";

export default function ComingSoon({ title, blurb }: { title: string; blurb: string }) {
  return (
    <div className="coming-soon">
      <p className="eyebrow">FlorAcco</p>
      <h1>{title}</h1>
      <p className="muted">{blurb}</p>
      <Link className="pill-button is-active" to="/">
        Back to home
      </Link>
    </div>
  );
}
