import { Fragment, type ReactNode } from "react";
import PairCircle from "./PairCircle";
import { CORSINI_CIRCLE } from "./corsiniCircle";
import EvidenceBars from "./EvidenceBars";
import { CORSINI_EVIDENCE } from "./corsiniEvidence";

/* The empty-pane primer: an argument for probabilistic record linkage told
 * in steps, each on real entries read from main.db (ids, years, firms and
 * weights are recorded beside the data they come from). Every worked pair
 * lies outside the open labeling packet, and the weights shown are the
 * cache's own, rounded, so the page never anchors a blind case. Shown by
 * intent (arrival, or the rail's "How this works"), never as a popup. */

// Evidence contributions as the tool draws them today, read from
// person_cache.db run pl-cdb536ef60e2cef33722 (model 17e49ecc…) on
// 2026-09-02, rounded to one decimal. Refresh these when the model is refit.
// Endnotes. A small amber number in the text jumps to its note at the foot
// of the primer; the note's return link jumps back to the sentence. The
// primer scrolls inside its own pane, so the jumps use scrollIntoView rather
// than URL hashes (a hash would reach the router), land instantly like a
// native footnote jump, and flash the arrival point briefly.
function jumpTo(id: string) {
  const target = document.getElementById(id);
  if (!target) return;
  target.scrollIntoView({ behavior: "auto", block: "center" });
  target.classList.add("is-flash");
  window.setTimeout(() => target.classList.remove("is-flash"), 1600);
}

// A weight in bits, written the way the charts draw it: a sign and one
// decimal, green for evidence in favour, rust for evidence against; sums and
// thresholds in the text colour. Approximate figures in the prose ("about
// ten bits") stay in words.
function Bits({ v, unit, sum }: { v: number; unit?: boolean; sum?: boolean }) {
  const sign = v < 0 ? "\u2212" : "+";
  const tone = sum ? "is-sum" : v < 0 ? "is-against" : "is-for";
  return <span className={`pl-bits ${tone}`}>{sign}{Math.abs(v).toFixed(1)}{unit ? " bits" : ""}</span>;
}

function Fn({ n }: { n: number }) {
  return (
    <sup className="pl-fn-ref" id={`pl-fnref-${n}`}>
      <a
        href={`#pl-fn-${n}`}
        aria-label={`Note ${n}`}
        onClick={(event) => {
          event.preventDefault();
          jumpTo(`pl-fn-${n}`);
        }}
      >
        {n}
      </a>
    </sup>
  );
}

function Notes({ notes }: { notes: ReactNode[] }) {
  return (
    <section className="pl-primer-panel pl-primer-notes" aria-labelledby="pl-notes-heading">
      <h3 id="pl-notes-heading">Notes</h3>
      <ol>
        {notes.map((note, index) => {
          const n = index + 1;
          return (
            <li key={n} id={`pl-fn-${n}`}>
              <span className="pl-fn-body">{note}</span>{" "}
              <a
                className="pl-fn-back"
                href={`#pl-fnref-${n}`}
                aria-label={`Back to the text at note ${n}`}
                title="Back to the text"
                onClick={(event) => {
                  event.preventDefault();
                  jumpTo(`pl-fnref-${n}`);
                }}
              >
                ↑
              </a>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

const PRIMER_NOTES: ReactNode[] = [
  (
    <>
      The 3,452 later acts (renewals, terminations and balances) name people
      too, but only in their narrative text. The database does not list who
      appears in them, so they are not counted here.
    </>
  ),
  (
    <>
      Sixty-one of the 11,391 person entries are not people: institutions
      such as the Arte della Lana, estates (&ldquo;the heirs of&hellip;&rdquo;),
      and placeholders such as &ldquo;MOLTEPLICI AZIONARI&rdquo;, the
      clerk&rsquo;s shorthand for many shareholders. The tool sets these aside
      before comparing anyone.
    </>
  ),
  (
    <>
      Applied to historical people, this is the working method of
      prosopography: the study of a group of people through the documents that
      name them. Here the group is the investors of Florence.
    </>
  ),
  (
    <>
      Ivan Fellegi and Alan Sunter, &ldquo;A Theory for Record Linkage&rdquo;,{" "}
      <em>Journal of the American Statistical Association</em> 64 (1969),
      1183&ndash;1210. For a gentle introduction, Robin Linacre&rsquo;s{" "}
      <a href="https://www.robinlinacre.com/intro_to_probabilistic_linkage/" target="_blank" rel="noreferrer">introduction to probabilistic linkage</a>{" "}
      and{" "}
      <a href="https://www.robinlinacre.com/partial_match_weights/" target="_blank" rel="noreferrer">partial match weights</a>.
    </>
  ),
  (
    <>
      Splink is an open-source implementation of the Fellegi&ndash;Sunter
      model from the UK Ministry of Justice:{" "}
      <a href="https://moj-analytical-services.github.io/splink/" target="_blank" rel="noreferrer">moj-analytical-services.github.io/splink</a>.
      It estimates the weights from this database and scores every candidate
      pair. The project&rsquo;s own rules and this review tool are built on
      top of it.
    </>
  ),
  (
    <>
      The actual decision that a human reviewer of these cases makes is then
      written down as a dated note under the reviewer&rsquo;s name, next to
      the two entries. No entry is ever forcefully merged or deleted.
    </>
  ),
  (
    <>
      The tool compares entries, and a single entry is the smallest thing it
      can work with. Taking one entry apart would mean re-attaching its
      contracts to new entries, a separate design that the project has set
      aside for now.
    </>
  ),
  (
    <>
      Measured on this database: among pairs of entries with the same full
      name, the gaps between their working lives fall into two groups, 2 to
      18 years (cousins named after the same grandfather) and about 50 years
      (the name returning two generations later). No pair with different
      grandfathers falls between 40 and 60 years.
    </>
  ),
  (
    <>
      Sixty years is the longest working life the tool allows one person.
      Only five person entries in the database span more, and the longest,
      Agnolo Guicciardini&rsquo;s 69 years, is the fused entry of the second
      section.
    </>
  ),
  (
    <>
      The whole name is compared with the Jaro&ndash;Winkler similarity, a
      score between 0 and 1 that counts the letters two spellings share, in
      order, with a bonus for a common beginning. A score of 0.92 or above
      counts as very close, 0.85 or above as moderately close. The
      father&rsquo;s name, and the fifth grouping of the next section, use the
      edit distance instead: the number of letters inserted, dropped or
      changed to turn one spelling into the other, allowed to be one.
    </>
  ),
  (
    <>
      A hand-made list of 83 spelling variants was built and set aside. For
      example, it paired <em>Iacob</em> with <em>Jacob</em>, <em>Cammillo</em> with <em>Camillo</em> and
      <em>Laldadio</em> with <em>Laudadio</em>; the one-letter rule finds all three by itself. The rule
      also sweeps in <em>Mario</em> and <em>Marco</em>, which are two names. The project measured that error and
      accepted it: a one-letter difference earns only <Bits v={3.9} unit />, and such
      pairs move from 2 in 1,000 to 4 in 100.
    </>
  ),
  (
    <>
      That &ldquo;about half&rdquo; is an assumption, called recall. It is the
      single biggest lever on every number on this page: assume the rule
      finds fewer of the true matches and the prior becomes less doubtful, so
      more pairs cross the threshold; assume it finds more and the prior
      becomes more doubtful. The project&rsquo;s rule is that no count is
      quoted without saying which recall it assumed.
    </>
  ),
  (
    <>
      Nine in ten is the threshold at which the tool groups entries into a
      proposed identity. Below it a pair still appears in the queue, ranked by
      its evidence, for a reviewer to judge.
    </>
  ),
  (
    <>
      These two numbers were chosen to make the arithmetic easy to follow. The
      fitted values appear on every case, and for the father&rsquo;s name they
      are different: the fit puts much of the weight on fathers who differ, a
      side effect of estimating without a single confirmed match, and one
      reason the first round of reviewed decisions matters.
    </>
  ),
  (
    <>
      Entries 11458 and 12322, both in contracts 1987 and 2052. The text of
      contract 1987 names a single Giovanni.
    </>
  ),
  (
    <>
      In symbols: weight = log<sub>2</sub>(<em>m</em> ÷ <em>u</em>); total =
      prior + the sum of the weights; probability = 2<sup>total</sup> ÷ (1 +
      2<sup>total</sup>). Some landmarks: &minus;10 bits is 1 in 1,000;
      &minus;3.3 is 1 in 10; 0 is even; +3.3 is 9 in 10; +10 is 999 in 1,000.
    </>
  ),
  (
    <>
      One known weakness: agreeing on the full name and agreeing on the
      father&rsquo;s name are partly the same fact, because families reuse
      both, and the model still counts them as two separate pieces of
      evidence. Until that is repaired its weights read somewhat higher than
      they should.
    </>
  ),
  (
    <>
      The five groupings are what record linkage calls blocking rules.
      Together they produce 15,637 pairs to score, from which the rules and
      the model build the queue. Gaps remain: two entries whose family names
      differ by one letter and whose fathers are unrecorded fall into none of
      the five groups. A sixth grouping for them is planned after the first
      round of decisions.
    </>
  ),
  (
    <>
      Partners are counted only if they are not what the project calls hubs:
      22 people appear in more than 20 contracts each (the Tempi brothers in
      60), and sharing one of them says almost nothing, so the tool leaves
      them out of the count. In the Corsini circle one partner is a hub,
      Filippo di Lorenzo Corsini with 24 contracts, and he is set aside.
      Partners are also counted by entry number, not by person, so a partner
      who was himself entered twice counts as two different people. Riccardo
      di Giovanni Riccardi, entries 1586 and 11824, appears in Tommaso
      Scarlattini e compagni in 1568 and 1572, each time beside a Francesco
      and a Bernardo di Giovanni Riccardi; the brothers were entered twice as
      well, so by entry number the two Riccardos share no partner at all. The
      firm&rsquo;s name, a piece of text, still links them.
    </>
  ),
  (
    <>
      The queue is ordered by an evidence score shown in four priority bands,
      not by the raw probability. A check of the fitted model found that its
      probabilities add up to about three times the number of true pairs
      implied by its own prior, which is why they are read as a ranking rather
      than as real odds.
    </>
  ),
  (
    <>
      The rules always take precedence. A pair the sixty-year rule refuses
      stays refused whatever the model scores, and a high score never removes
      a caution. The model orders what is undecided; it does not overrule.
    </>
  ),
  (
    <>
      An answer is written to a separate store, not to the database
      itself, and that store survives every rebuild of the database. It
      consists of a link between entry numbers and one dated event with the
      reviewer&rsquo;s initials and reason. Undo adds a second event; it does
      not delete the first.
    </>
  ),
];

// The bits ruler: two framed cases side by side on one compact scale. The
// gutter at the left carries each tick as bits over odds; inside each case
// the rust bar is the prior and each green bar one comparison,
// stacked on the last. Weights are the cache's own (run
// pl-cdb536ef60e2cef33722, 2026-09-02).
const RULER = { min: -21, max: 12, top: 62, bottom: 352 };
const rulerY = (bits: number) =>
  RULER.top + ((RULER.max - bits) / (RULER.max - RULER.min)) * (RULER.bottom - RULER.top);
const RULER_TICKS = [
  { bits: 10, odds: "999 in 1,000" },
  { bits: 3.2, odds: "9 in 10" },
  { bits: 0, odds: "even odds" },
  { bits: -3.3, odds: "1 in 10" },
  { bits: -10, odds: "1 in 1,000" },
  { bits: -19.2, odds: "1 in 600,000" },
];
const AXIS_X = 98;
const CASE_PANELS = [
  { x: 112, width: 388 },
  { x: 518, width: 388 },
];
const CLIMBS = [
  {
    title: "Andrea di Neri Corsini",
    subtitle: "entries 5305 and 11633",
    steps: [
      { label: "name", bits: 10.0 },
      { label: "father,|grandfather", bits: 7.2 },
      { label: "years & firm", bits: 9.8 },
      { label: "role", bits: 0.7 },
    ],
    total: "+8.5 bits · 997 in 1,000",
  },
  {
    title: "Tedice degli Albizzi",
    subtitle: "entries 56 and 12058",
    steps: [
      { label: "name", bits: 10.6 },
      { label: "father,|grandfather", bits: 10.8 },
    ],
    total: "+2.2 bits · 82 in 100",
  },
];
const BAR_W = 40;
const BAR_STEP = 62;
const DOUBT = -19.2;
const LINE_BITS = 3.2;

function BitsRuler() {
  return (
    <figure className="pl-ruler" id="pl-primer-ruler">
      <svg
        viewBox="0 0 920 392"
        role="img"
        aria-label={
          "Two framed cases on one scale in bits, from minus 21 to plus 12, with odds " +
          "at the ticks: plus 10 is 999 in 1,000; plus 3.2 is nine in ten, the tool's " +
          "line; zero is even odds; minus 19.2 is one in 600,000, the prior. " +
          "Corsini, entries 5305 and 11633: from the doubt, name plus 10.0, father and " +
          "grandfather plus 7.2, years and firm plus 9.8, role plus 0.7, ending at plus " +
          "8.5 bits, above the line. Albizzi, entries 56 and 12058: name plus 10.6, " +
          "father and grandfather plus 10.8, ending at plus 2.2 bits, just under it."
        }
      >
        <line className="pl-ruler-axis" x1={AXIS_X} y1={RULER.top - 8} x2={AXIS_X} y2={RULER.bottom + 8} />
        {RULER_TICKS.map((tick) => (
          <g key={tick.bits}>
            <line className="pl-ruler-tick" x1={AXIS_X - 5} y1={rulerY(tick.bits)} x2={AXIS_X} y2={rulerY(tick.bits)} />
            <text className="pl-ruler-bits" x={AXIS_X - 10} y={rulerY(tick.bits) - 3} textAnchor="end">
              {tick.bits > 0 ? `+${tick.bits}` : tick.bits}
            </text>
            <text className={`pl-ruler-odds ${tick.bits === DOUBT ? "is-against" : ""}`} x={AXIS_X - 10} y={rulerY(tick.bits) + 11} textAnchor="end">
              {tick.odds}
            </text>
          </g>
        ))}
        {CLIMBS.map((climb, index) => {
          const panel = CASE_PANELS[index];
          const left = panel.x;
          const right = panel.x + panel.width;
          let level = DOUBT;
          const bars = climb.steps.map((step, i) => {
            const from = level;
            level += step.bits;
            return { ...step, from, to: level, x: left + 22 + (i + 1) * BAR_STEP };
          });
          const last = bars[bars.length - 1];
          const totalWidth = climb.total.length * 6.6;
          const totalFitsRight = last.x + BAR_W + 10 + totalWidth < right - 8;
          return (
            <g key={climb.title}>
              <rect className="pl-ruler-case" x={left} y="6" width={panel.width} height="380" rx="10" />
              <text className="pl-ruler-title" x={left + 14} y="27">{climb.title}</text>
              <text className="pl-ruler-subtitle" x={right - 14} y="27" textAnchor="end">{climb.subtitle}</text>
              <line className="pl-ruler-head-rule" x1={left} y1="40" x2={right} y2="40" />
              <rect className="pl-ruler-hole" x={left + 1} y={rulerY(0)} width={panel.width - 2} height={385 - rulerY(0)} />
              <line className="pl-ruler-even" x1={left} y1={rulerY(0)} x2={right} y2={rulerY(0)} />
              <line className="pl-ruler-line" x1={left} y1={rulerY(LINE_BITS)} x2={right} y2={rulerY(LINE_BITS)} />
              <text className="pl-ruler-linelabel" x={left + 10} y={rulerY(LINE_BITS) - 5}>threshold: 9 in 10</text>
              <text className="pl-ruler-evenlabel" x={right - 10} y={rulerY(0) + 12} textAnchor="end">below: more likely two people</text>
              <rect className="pl-ruler-doubt" x={left + 22} y={rulerY(0)} width={BAR_W} height={rulerY(DOUBT) - rulerY(0)} rx="3" />
              <text className="pl-ruler-step" x={left + 22 + BAR_W / 2} y={rulerY(DOUBT) + 14} textAnchor="middle">{DOUBT}</text>
              <text className="pl-ruler-step-word" x={left + 22 + BAR_W / 2} y={rulerY(DOUBT) + 26} textAnchor="middle">the</text>
              <text className="pl-ruler-step-word" x={left + 22 + BAR_W / 2} y={rulerY(DOUBT) + 38} textAnchor="middle">prior</text>
              {bars.map((bar, i) => (
                <g key={bar.label}>
                  <line className="pl-ruler-join" x1={(i === 0 ? left + 22 : bars[i - 1].x) + BAR_W} y1={rulerY(bar.from)} x2={bar.x} y2={rulerY(bar.from)} />
                  <rect className="pl-ruler-gain" x={bar.x} y={rulerY(bar.to)} width={BAR_W} height={Math.max(2, rulerY(bar.from) - rulerY(bar.to))} rx="3" />
                  <text className="pl-ruler-step" x={bar.x + BAR_W / 2} y={rulerY(bar.from) + 14} textAnchor="middle">+{bar.bits.toFixed(1)}</text>
                  {bar.label.split("|").map((line, k) => (
                    <text key={line} className="pl-ruler-step-word" x={bar.x + BAR_W / 2} y={rulerY(bar.from) + 26 + k * 12} textAnchor="middle">{line}</text>
                  ))}
                </g>
              ))}
              {totalFitsRight ? (
                <text className="pl-ruler-total" x={last.x + BAR_W + 10} y={rulerY(level) + 4}>{climb.total}</text>
              ) : (
                <text className="pl-ruler-total" x={last.x + BAR_W / 2} y={rulerY(level) - 9} textAnchor="middle">{climb.total}</text>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption>
        Two real pairs on one scale, each in its own frame. The rust bar is
        the prior that every pair starts from; each green bar is one
        comparison, added to what came before; the tinted area below even
        odds is where a pair is more likely two people than one; and the
        dashed line at nine in ten is the threshold above which the tool
        treats a pair as likely the same person. The Albizzi pair, one of
        whose entries has no contracts, runs out of evidence just below it.
      </figcaption>
    </figure>
  );
}

// The pair panel's records: Andrea di Neri Corsini, 5305 and 11633, read
// from main.db 2026-09-02. 5305: acts 1631 c2522, 1634 c2571, 1636 c2614,
// 1638 c2739 (Benedetto Nomi e Tommaso Pandolfini e compagni), 1644 c3513;
// lp in all five. 11633: acts 1645 c4037 (the same firm), 1652 c3556, 1657
// c3635; lp in all three. Seven partners recur by entry id. Neither entry
// is in the v3 labeling packet; the pair sits in the review tier.
type PairRow = { field: string; a: string | null; b: string | null } | { field: string; span: string };

const CORSINI_ROWS: PairRow[] = [
  { field: "Given name", a: "Andrea", b: "Andrea" },
  { field: "Father", a: "Neri", b: "Neri" },
  { field: "Grandfather", a: null, b: "Lorenzo" },
  { field: "Family name", a: "Corsini", b: "Corsini" },
  { field: "Active", a: "1631\u20131644", b: "1645\u20131657" },
  { field: "Acts", a: "5", b: "3" },
  { field: "Role", a: "external investor in all 5", b: "external investor in all 3" },
  { field: "Firms", a: "5 firms, among them Benedetto Nomi e Tommaso Pandolfini e compagni (1638)", b: "3 firms, among them Benedetto Nomi e Tommaso Pandolfini e compagni (1645)" },
  { field: "Shared partners", span: "7 by entry number, among them Benedetto Nomi, Tommaso Pandolfini, Luca and Agostino Franceschi, Carlo and Giovanni Rinuccini" },
  { field: "Husband", a: null, b: null },
];

function PairGrid({ rows, headA, headB, caption, ariaLabel }: {
  rows: PairRow[];
  headA: string;
  headB: string;
  caption: string;
  ariaLabel: string;
}) {
  return (
    <figure className="pl-cmp" aria-label={ariaLabel}>
      <div className="pl-cmp-grid">
        <span className="pl-cmp-head" aria-hidden="true">Field</span>
        <span className="pl-cmp-head" aria-hidden="true">{headA}</span>
        <span className="pl-cmp-head" aria-hidden="true">{headB}</span>
        {rows.map((row) => (
          <Fragment key={row.field}>
            <span className="pl-cmp-field">{row.field}</span>
            {"span" in row ? (
              <span className="is-span">{row.span}</span>
            ) : (
              <>
                <span className={row.a ? "" : "is-blank"}>{row.a ?? "not recorded"}</span>
                <span className={row.b ? "" : "is-blank"}>{row.b ?? "not recorded"}</span>
              </>
            )}
          </Fragment>
        ))}
      </div>
      <figcaption>{caption}</figcaption>
    </figure>
  );
}

// Panel 1: the three shapes, drawn abstractly. One vocabulary for all three:
// a small figure is a person, a card is a database entry, a bracket is
// "was entered as". Only the count and the direction change between the
// three, which is the whole lesson. No ids or years here on purpose; the
// intro above and the panels below carry the real cases.
function PersonMark({ cx }: { cx: number }) {
  return (
    <g className="pl-pg-person">
      <circle cx={cx} cy="14" r="4.5" />
      <path d={`M${cx - 8} 30 A 8 8 0 0 1 ${cx + 8} 30 Z`} />
    </g>
  );
}

function RecordMark({ cx, ghost }: { cx: number; ghost?: boolean }) {
  return (
    <g className={ghost ? "pl-pg-record is-ghost" : "pl-pg-record"}>
      <rect x={cx - 22} y="62" width="44" height="32" rx="5" />
      <rect x={cx - 14} y="72" width="24" height="3" rx="1.5" className="pl-pg-record-line" />
      <rect x={cx - 14} y="80" width="16" height="3" rx="1.5" className="pl-pg-record-line" />
    </g>
  );
}

function ShapeCards() {
  return (
    <div className="pl-pg-faces">
      <figure className="pl-pg-face">
        <svg viewBox="0 0 240 100" role="img" aria-label="One person mark above a bracket that forks down into two entry cards.">
          <PersonMark cx={120} />
          <path className="pl-pg-bracket" d="M120 34 V48 M78 62 V52 Q78 48 82 48 H158 Q162 48 162 52 V62" />
          <RecordMark cx={78} />
          <RecordMark cx={162} />
        </svg>
        <figcaption><strong>Split.</strong> One person is spread over two or more entries, because the name was spelled differently or a father&rsquo;s name was left out.</figcaption>
      </figure>
      <figure className="pl-pg-face">
        <svg viewBox="0 0 240 100" role="img" aria-label="Two person marks above a bracket that merges down into one entry card.">
          <PersonMark cx={78} />
          <PersonMark cx={162} />
          <path className="pl-pg-bracket" d="M78 34 V44 Q78 48 82 48 H158 Q162 48 162 44 V34 M120 48 V62" />
          <RecordMark cx={120} />
        </svg>
        <figcaption><strong>Fused.</strong> Two different people sit in one entry, because their names agree and nothing in the contracts set them apart.</figcaption>
      </figure>
      <figure className="pl-pg-face">
        <svg viewBox="0 0 240 100" role="img" aria-label="One person mark above a bracket that fans down into four entry cards, the first solid and the other three dashed.">
          <PersonMark cx={120} />
          <path className="pl-pg-bracket" d="M120 34 V48 M48 62 V52 Q48 48 52 48 H188 Q192 48 192 52 V62 M96 48 V62 M144 48 V62" />
          <RecordMark cx={48} />
          <RecordMark cx={96} ghost />
          <RecordMark cx={144} ghost />
          <RecordMark cx={192} ghost />
        </svg>
        <figcaption><strong>Copies.</strong> One person was entered several times in a row; the extra entries appear in no contract.</figcaption>
      </figure>
    </div>
  );
}

// The intro example: six person entries from one family, read from main.db
// on 2026-09-02 (verbatim spellings; "active" = first to last dated act,
// posthumous rows excluded). None of the six sits in the open labeling
// packet, and the readings below agree with the deterministic rules: the two
// Cosimos are refused by the 60-year career guard; the spelling pairs are
// review-tier candidates found by the surname-variant lane.
type ExampleRecord = {
  id: number;
  given: string;
  father: string;
  grandfather: string | null;
  family: string;
  active: string;
  acts: number;
  key: "family" | "father" | "active";
};

const EXAMPLE_GROUPS: Array<{ label: string; records: ExampleRecord[] }> = [
  {
    label: "One man, spelled two ways",
    records: [
      { id: 12135, given: "Jacopo", father: "Giovanni Battista", grandfather: "Bartolomeo", family: "Riccardi", active: "1582", acts: 1, key: "family" },
      { id: 2959, given: "Jacopo", father: "Giovanni Battista", grandfather: null, family: "Ricciardi", active: "1593\u20131611", acts: 7, key: "family" },
    ],
  },
  {
    label: "One man, spelled two ways, in the same year",
    records: [
      { id: 4458, given: "Francesco", father: "Jacopo", grandfather: null, family: "Ricciardi", active: "1611\u20131627", acts: 5, key: "family" },
      { id: 11100, given: "Francesco", father: "Jacopo", grandfather: null, family: "Riccardi", active: "1627", acts: 2, key: "family" },
    ],
  },
  {
    label: "Two men: the same name, 121 years apart",
    records: [
      { id: 4340, given: "Cosimo", father: "Francesco", grandfather: null, family: "Riccardi", active: "1612\u20131622", acts: 10, key: "active" },
      { id: 11024, given: "Cosimo", father: "Francesco", grandfather: null, family: "Riccardi", active: "1733", acts: 1, key: "active" },
    ],
  },
];

// Where two spellings differ by a letter or two, mark those letters. The two
// strings are aligned on their longest common subsequence and whatever is
// left over on each side is the difference; if more than three letters are
// left over the names are simply different words and nothing is marked.
function spellingMarks(a: string, b: string): [boolean[], boolean[]] {
  const n = a.length;
  const m = b.length;
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const marksA = new Array<boolean>(n).fill(true);
  const marksB = new Array<boolean>(m).fill(true);
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      marksA[i] = false;
      marksB[j] = false;
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      i += 1;
    } else {
      j += 1;
    }
  }
  const leftOver = marksA.filter(Boolean).length + marksB.filter(Boolean).length;
  if (leftOver === 0 || leftOver > 3) return [marksA.map(() => false), marksB.map(() => false)];
  return [marksA, marksB];
}

function Spelled({ text, marks }: { text: string; marks: boolean[] }) {
  const parts: Array<{ text: string; marked: boolean }> = [];
  for (let i = 0; i < text.length; i += 1) {
    const last = parts[parts.length - 1];
    if (last && last.marked === marks[i]) last.text += text[i];
    else parts.push({ text: text[i], marked: marks[i] });
  }
  return (
    <>
      {parts.map((part, i) =>
        part.marked ? <span className="pl-ex-diff" key={i}>{part.text}</span> : <Fragment key={i}>{part.text}</Fragment>,
      )}
    </>
  );
}

function RecordPairs({
  groups,
  caption,
  ariaLabel,
}: {
  groups: Array<{ label: string; records: ExampleRecord[] }>;
  caption: string;
  ariaLabel: string;
}) {
  return (
    <figure className="pl-ex" aria-label={ariaLabel}>
      <div className="pl-ex-scroll">
        <div className="pl-ex-head" aria-hidden="true">
          <span className="pl-ex-row">
            <span>Entry</span>
            <span>Given name</span>
            <span>Father</span>
            <span>Grandfather</span>
            <span>Family name</span>
            <span>Active</span>
            <span>Acts</span>
          </span>
          <span />
          <span />
        </div>
        {groups.map((group) => {
          const spelled =
            group.records.length === 2 && group.records[0].key === "family"
              ? spellingMarks(group.records[0].family, group.records[1].family)
              : null;
          return (
          <div className="pl-ex-group" key={group.label}>
            <div className="pl-ex-rows">
              {group.records.map((record, index) => (
                <span className="pl-ex-row" key={record.id}>
                  <span className="pl-ex-id">#{record.id}</span>
                  <span>{record.given}</span>
                  <span className={record.key === "father" ? "is-key" : ""}>{record.father}</span>
                  <span className={record.grandfather ? "" : "is-blank"}>{record.grandfather ?? "\u2014"}</span>
                  <span className={record.key === "family" ? "is-key" : ""}>
                    {spelled ? <Spelled text={record.family} marks={spelled[index]} /> : record.family}
                  </span>
                  <span className={record.key === "active" ? "is-key" : ""}>{record.active}</span>
                  <span className="pl-ex-acts">{record.acts}</span>
                </span>
              ))}
            </div>
            <span className="pl-ex-brace" aria-hidden="true" />
            <span className="pl-ex-label">{group.label}</span>
          </div>
          );
        })}
      </div>
      <figcaption>{caption}</figcaption>
    </figure>
  );
}

// The naming panel's second example: two Cosimo Riccardi in the same two
// firms (Andrea Albizzini e compagni 1613/1618, Francesco Malingegni e
// compagni 1615/1618), one the son of Francesco and one of Giovanni. Read
// from main.db 2026-09-02; the pair is review-tier, not in the packet.
const COUSIN_GROUPS: Array<{ label: string; records: ExampleRecord[] }> = [
  {
    label: "Same name, same firms, different fathers",
    records: [
      { id: 4340, given: "Cosimo", father: "Francesco", grandfather: null, family: "Riccardi", active: "1612\u20131622", acts: 10, key: "father" },
      { id: 4697, given: "Cosimo", father: "Giovanni", grandfather: null, family: "Riccardi", active: "1618", acts: 5, key: "father" },
    ],
  },
];

// A dot timeline in the shapes panel's grammar: a card is an entry, a
// bracket ties it to its own dated acts, a dot is one act, a ring marks an
// act singled out by the caption. Nothing is drawn between acts.
type TimelineAxis = { from: number; to: number; x0: number; x1: number };
type TimelineRecord = {
  id: number;
  years: number[];
  ring?: number[];
  // years to print beneath their dot; "start" sets the numeral just right
  // of the dot, for a dot that has a connector hanging from it
  labels?: Array<{ year: number; anchor?: "start" | "middle" }>;
};
type TimelineRow = { label: string; y: number; records: TimelineRecord[] };
type TimelineLink = { from: { y: number; year: number }; to: { y: number; year: number }; label: string };
type TimelineMeasure = { y: number; from: number; to: number; label: string };

const TL_CHIP_W = 78;

function TimelineRecordMarks({ record, y, x }: { record: TimelineRecord; y: number; x: (year: number) => number }) {
  const byYear = new Map<number, number>();
  record.years.forEach((year) => byYear.set(year, (byYear.get(year) ?? 0) + 1));
  const distinct = [...byYear.keys()].sort((a, b) => a - b);
  const left = x(distinct[0]);
  const right = x(distinct[distinct.length - 1]);
  const chipX = Math.min(Math.max((left + right) / 2 - TL_CHIP_W / 2, 276), 920 - 8 - TL_CHIP_W);
  const chipTop = y - 40;
  const chipMid = chipX + TL_CHIP_W / 2;
  const bracket = left === right
    ? `M${left} ${chipTop + 18} V${y - 8}`
    : `M${chipMid} ${chipTop + 18} V${y - 14} M${left} ${y - 8} V${y - 11} Q${left} ${y - 14} ${left + 3} ${y - 14} H${right - 3} Q${right} ${y - 14} ${right} ${y - 11} V${y - 8}`;
  return (
    <g>
      <rect className="pl-tl-chip" x={chipX} y={chipTop} width={TL_CHIP_W} height="18" rx="4" />
      <text className="pl-tl-chip-text" x={chipMid} y={chipTop + 12.5} textAnchor="middle">entry {record.id}</text>
      <path className="pl-pg-bracket" d={bracket} />
      {distinct.map((year) => {
        const count = byYear.get(year) ?? 1;
        const ring = record.ring?.includes(year);
        return (
          <g key={year}>
            <circle className={ring ? "pl-tl-ring" : "pl-career-point"} cx={x(year)} cy={y} r={count > 1 ? 6.5 : 4.5} />
            {count > 1 ? (
              <text className="pl-career-count" x={x(year)} y={y + 2.5} textAnchor="middle">{count}</text>
            ) : null}
          </g>
        );
      })}
      {(record.labels ?? []).map((label) => (
        <text
          key={label.year}
          className="pl-tl-year"
          x={label.anchor === "start" ? x(label.year) + 5 : x(label.year)}
          y={y + 17}
          textAnchor={label.anchor ?? "middle"}
        >
          {label.year}
        </text>
      ))}
    </g>
  );
}

function DotTimeline({ axis, ticks, rows, link, measure, ariaLabel, caption }: {
  axis: TimelineAxis;
  ticks: number[];
  rows: TimelineRow[];
  link?: TimelineLink;
  measure?: TimelineMeasure;
  ariaLabel: string;
  caption: ReactNode;
}) {
  const x = (year: number) => axis.x0 + ((year - axis.from) / (axis.to - axis.from)) * (axis.x1 - axis.x0);
  return (
    <figure className="pl-tl">
      <svg viewBox="0 0 920 178" role="img" aria-label={ariaLabel}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="pl-career-tick" x1={x(tick)} y1="10" x2={x(tick)} y2="154" />
            <text className="pl-career-axis-year" x={x(tick)} y="172" textAnchor="middle">{tick}</text>
          </g>
        ))}
        <line className="pl-career-axis" x1={axis.x0} y1="156" x2={axis.x1} y2="156" />
        {rows.map((row) => (
          <g key={row.label}>
            <text className="pl-tl-chain" x="14" y={row.y + 4}>{row.label}</text>
            {row.records.map((record) => (
              <TimelineRecordMarks key={record.id} record={record} y={row.y} x={x} />
            ))}
          </g>
        ))}
        {link ? (
          <g>
            <path
              className="pl-tl-link"
              d={`M${x(link.from.year)} ${link.from.y + 8} V${(link.from.y + link.to.y) / 2} H${x(link.to.year)} V${link.to.y - 8}`}
            />
            <text
              className="pl-tl-link-label"
              x={x(link.from.year) - 9}
              y={(link.from.y + link.to.y) / 2 + 3.5}
              textAnchor="end"
            >
              {link.label}
            </text>
          </g>
        ) : null}
        {measure ? (
          <g>
            <line className="pl-tl-measure" x1={x(measure.from)} y1={measure.y} x2={x(measure.to)} y2={measure.y} />
            <line className="pl-tl-measure" x1={x(measure.from)} y1={measure.y - 4} x2={x(measure.from)} y2={measure.y + 4} />
            <line className="pl-tl-measure" x1={x(measure.to)} y1={measure.y - 4} x2={x(measure.to)} y2={measure.y + 4} />
            <text className="pl-tl-note" x={(x(measure.from) + x(measure.to)) / 2} y={measure.y + 11} textAnchor="middle">
              {measure.label}
            </text>
          </g>
        ) : null}
      </svg>
      <figcaption>{caption}</figcaption>
    </figure>
  );
}

// The Torrigiani: two identical chains alternating across a century. Act
// years read from main.db 2026-09-02 (living appearances only): entry 246
// Raffaello di Luca di Raffaello, acts 1532 (2), 1535, 1538, 1544, 1579,
// 1580, 1582; 761 Luca di Raffaello di Luca, 1546, 1549; 12307 the same
// chain, 1572, 1595 (2); 12311 Raffaello di Luca di Raffaello, 1601, 1612,
// 1613, 1615; 12308 Luca di Raffaello di Luca, 1638.
function TorrigianiFigure() {
  return (
    <DotTimeline
      axis={{ from: 1530, to: 1640, x0: 296, x1: 900 }}
      ticks={[1540, 1560, 1580, 1600, 1620, 1640]}
      rows={[
        {
          label: "Raffaello di Luca di Raffaello",
          y: 58,
          records: [
            { id: 246, years: [1532, 1532, 1535, 1538, 1544, 1579, 1580, 1582] },
            { id: 12311, years: [1601, 1612, 1613, 1615] },
          ],
        },
        {
          label: "Luca di Raffaello di Luca",
          y: 120,
          records: [
            { id: 761, years: [1546, 1549] },
            { id: 12307, years: [1572, 1595, 1595] },
            { id: 12308, years: [1638] },
          ],
        },
      ]}
      measure={{ y: 140, from: 1546, to: 1638, label: "92 years from the first Luca to the last" }}
      ariaLabel={
        "Five Torrigiani entries on one axis from 1530 to 1640, in two rows by " +
        "full name. Raffaello di Luca di Raffaello: entry 246 with contracts from " +
        "1532 to 1582, entry 12311 with contracts from 1601 to 1615. Luca di " +
        "Raffaello di Luca: entry 761 with contracts in 1546 and 1549, entry 12307 " +
        "with contracts in 1572 and 1595, entry 12308 with one contract in 1638. The " +
        "first and last Luca are 92 years apart."
      }
      caption={
        <>
          Each card is one person entry, with its number in the database.
          The dots below a card are the contracts that entry appears in,
          one dot per dated contract (a 2 marks two contracts in one year).
          Nothing is drawn between the dots: the registers say nothing about
          the years in between.
        </>
      }
    />
  );
}

// The Corsini pair's years: 5305 acts 1631, 1634, 1636, 1638, 1644; 11633
// acts 1645, 1652, 1657. The ringed acts, 1638 and 1645, are both in
// Benedetto Nomi e Tommaso Pandolfini e compagni.
function CorsiniFigure() {
  return (
    <DotTimeline
      axis={{ from: 1628, to: 1660, x0: 296, x1: 900 }}
      ticks={[1630, 1640, 1650, 1660]}
      rows={[
        {
          label: "Andrea di Neri Corsini",
          y: 58,
          records: [{ id: 5305, years: [1631, 1634, 1636, 1638, 1644], ring: [1638], labels: [{ year: 1638, anchor: "start" }, { year: 1644 }] }],
        },
        {
          label: "Andrea di Neri di Lorenzo Corsini",
          y: 120,
          records: [{ id: 11633, years: [1645, 1652, 1657], ring: [1645], labels: [{ year: 1645 }] }],
        },
      ]}
      link={{ from: { y: 58, year: 1638 }, to: { y: 120, year: 1645 }, label: "the same firm, renewed" }}
      ariaLabel={
        "Two entries on one axis from 1628 to 1660. Entry 5305, Andrea di Neri " +
        "Corsini, with contracts in 1631, 1634, 1636, 1638 and 1644. Entry 11633, " +
        "Andrea di Neri di Lorenzo Corsini, with contracts in 1645, 1652 and 1657. " +
        "The years 1644 and 1645, one apart, are printed under their dots. The " +
        "contracts of 1638 and 1645 are green and joined by a dashed connector " +
        "labelled the same firm, renewed: both are in Benedetto Nomi e Tommaso " +
        "Pandolfini e compagni."
      }
      caption={
        <>
          Entry 5305 is last seen in 1644; entry 11633 first appears in
          1645. The two green contracts, 1638 and 1645, are both in{" "}
          <em>Benedetto Nomi e Tommaso Pandolfini e compagni</em>, renewed
          seven years apart.
        </>
      }
    />
  );
}

export default function PersonLinkagePrimer() {
  return (
    <article className="pl-primer">
      <p className="eyebrow">Person identity review</p>
      <h2>One person, several entries</h2>
      <div className="pl-primer-intro">
        <p>
          Each contract in this database names the people behind a firm, its{" "}
          <span className="term">general partners</span> and its{" "}
          <span className="term">external investors</span>. Across the 4,866
          contracts that comes to 17,495 appearances. Of course, the same
          person can appear on multiple contracts over many years.
          <Fn n={1} /> The registers never give these people a unique person
          identifier; what is recorded is a given name, usually a
          father&rsquo;s name, sometimes the grandfather&rsquo;s, and a family
          name (all of these spelled in a way the scribe chose, since spelling
          was not standardized in Italy until the nineteenth century). When
          the registers were typed into the database, an appearance was
          attached to an existing person entry where the typist recognized
          the name. Where the name was not recognized, or was spelled
          differently, a new person entry was created. The database therefore
          holds 11,391 person entries. Simply counting those would count many
          people twice.<Fn n={2} />
        </p>
        <p>
          The table below shows a simple example: six different person
          entries in the database, in all likelihood from one family, with
          the family name spelled in two ways.
        </p>
        <RecordPairs
          groups={EXAMPLE_GROUPS}
          caption="Six person entries with the family name Riccardi, exactly as they were entered. A dash means the contract did not record that name."
          ariaLabel="Six person entries of the Riccardi family, grouped into three pairs: two pairs that are one man each under two spellings, and one pair that is two men of identical name 121 years apart."
        />
        <p>
          Read together, the six entries most likely describe four people.
          Within the Jacopo pair and within the Francesco pair the given name
          and the father&rsquo;s name agree, and the family name is spelled in
          two ways; one Jacopo also records a grandfather, Bartolomeo, where
          the other records none. The years of the contracts they appear in follow on
          without overlap: the second Jacopo&rsquo;s first contract comes
          eleven years after the first Jacopo&rsquo;s only one, and the second
          Francesco&rsquo;s first contract falls in the very year of the first
          Francesco&rsquo;s last. The two Cosimos carry exactly the same name,
          but their contracts lie 121 years apart, more than a human life
          allows. Nothing in the database links the first two pairs or
          separates the third. A reader who sees these six entries side by
          side draws those conclusions at a glance, from the spellings, the
          names and the years. With 11,391 entries the same task outgrows any
          reader: there is too much to keep in mind and far too many pairs to
          compare. No reader can do it by hand.

        </p>
        <p>
          <span className="term">Probabilistic record linkage</span> is the
          method for this kind of problem: deciding, from the evidence, which
          entries without a shared identifier describe the same person.
          <Fn n={3} /> The method is called &ldquo;probabilistic&rdquo; because
          it never declares two entries to be the same person; it estimates
          how probable that is. Every point on which two entries agree or
          disagree is treated as a clue, and each clue is judged by two
          things: how typical it is of two entries that really do describe
          one person, and how easily it could arise, by accident, between two
          strangers. A
          clue that true matches nearly always show and strangers rarely do
          counts for a great deal; a clue that strangers show just as often
          counts for nothing. The clues are then added up, and the sum is the
          probability that the two entries describe one person. The{" "}
          <span className="term">Fellegi&ndash;Sunter model</span>
          <Fn n={4} /> is the form of the method that turns these two
          judgements into arithmetic; it is fitted to this database with a
          software library called <span className="term">Splink</span>.<Fn n={5} /> The method decides
          nothing on its own; in other words, it never forces a link between
          two entries. It puts the pairs of entries most likely to be one
          person in front of a human reviewer, together with the evidence,
          and the reviewer decides whether the suggestion adds up.<Fn n={6} />
        </p>
      </div>

      <section className="pl-primer-panel pl-primer-body">
        <h3>Three shapes the problem takes</h3>
        <p>
          The problem comes in three shapes. All three arose when the
          registers were typed into the database:
        </p>
        <ShapeCards />
        <p>
          With probabilistic linkage we can find split entries and link them,
          so that from then on they count as one person (both entries stay in
          the database as they were; nothing about the entries{" "}
          <em>themselves</em> changes). Copies are found and linked in the
          same way. When an entry seems to combine evidence that points to two
          different people, a potential fused entry, we can flag it for
          further attention (we do not take it apart).<Fn n={7} />
        </p>
      </section>

      <section className="pl-primer-panel pl-primer-body">
        <h3>Why a name alone cannot settle it</h3>
        <p>
          Florentine families named the eldest son after his paternal
          grandfather. As a result the same full name, given name,
          father&rsquo;s name and family name, comes back every second
          generation.<Fn n={8} /> The Torrigiani family shows how this works
          over the course of roughly a century, and the figure below places
          its five person entries on one time line. The given names <em>Raffaello</em>
          and <em>Luca</em> alternate, and with them the whole chain: a Raffaello di
          Luca di Raffaello,
          active from 1532; a Luca di Raffaello di Luca, from 1546; a Raffaello
          di Luca di Raffaello again, from 1601; and a Luca di Raffaello di
          Luca once more, in 1638. The first Luca and the last are 92 years
          apart. They cannot be one man, although the database gives them
          exactly the same name. So the same full name fifty years on is
          evidence of two people rather than one, and the tool treats it that
          way: if two entries together would mean a working life of more than
          sixty years, they are never proposed as one person.<Fn n={9} />
        </p>
        <TorrigianiFigure />
        <p>
          The opposite case is quieter. Two person entries named Cosimo
          Riccardi appear in the same two firms, Andrea Albizzini e compagni
          and Francesco Malingegni e compagni, five years apart. One is the
          son of Francesco, the other of Giovanni. Everything except the
          father&rsquo;s name would join them, as the table below shows. The
          father&rsquo;s name, <em>di Francesco</em> or <em>di Giovanni</em>,
          is the nearest thing the registers have to an identifier, and here
          it is the only thing that keeps the two apart. The tool does not
          merge such a pair; it sends it to a reviewer with both fathers in
          view.
        </p>
        <RecordPairs
          groups={COUSIN_GROUPS}
          caption="Two person entries, as entered. Both appear in Andrea Albizzini e compagni (in 1613 and 1618) and in Francesco Malingegni e compagni (in 1615 and 1618)."
          ariaLabel="Two person entries named Cosimo Riccardi, active in the same decade and the same firms, one the son of Francesco and one the son of Giovanni."
        />
        <p>
          Spelling misleads in different directions. <em>Riccardi</em> and <em>Ricciardi</em>, in
          the first example, are one letter apart and one man. <em>Antonio</em> and
          <em>Antonia</em> are one letter apart and a man and a woman (in Italian the
          final vowel marks the gender). A simple edit-distance measure such
          as Levenshtein, which counts the letters that must change to turn
          one spelling into the other, cannot tell the two cases apart: one
          letter in each. Nor can the finer score the tool uses for whole
          names (on which <em>Riccardi</em> and <em>Ricciardi</em> reach 0.93 and <em>Antonio</em>
          and <em>Antonia</em> 0.94).<Fn n={10} /> The tool does two things so that
          spellings can still be compared. First, closeness is graded: two
          names that agree exactly are a strong clue that the entries
          describe one person; two names one letter apart are a weaker clue
          (how much weaker is not decided by hand but learned from the
          database, as the next section explains). Second, one
          rule of Italian is written into the comparison of fathers&rsquo;
          names: a change of the final letter alone is not a spelling
          variant, because <em>-o</em> and <em>-a</em> mark
          gender and <em>-i</em> and <em>-o</em> mark a family
          against an individual. <em>Pagolo</em> and <em>Paolo</em> are one name; <em>Francesco</em> and
          <em>Francesca</em> are two.<Fn n={11} />
        </p>
      </section>

      <section className="pl-primer-panel pl-primer-body">
        <h3>How the evidence is weighed</h3>
        <p>
          Before it compares anything, the tool starts from an assumption
          about any two person entries: they are almost certainly not the same
          person. This starting assumption is called the{" "}
          <span className="term">prior</span>. Take two of the 11,391 entries
          at random. There are 64 million possible pairs, and by the
          tool&rsquo;s own estimate only about 110 of them are the same
          person. A strict rule, the
          same full name and a combined working life of under sixty years,
          finds a small number of sure matches, and the model assumes that
          this rule finds about half of all the matches that exist.
          <Fn n={12} /> One pair in 600,000 means odds of one to 600,000. The
          tool works with the logarithm of the odds, in units called{" "}
          <span className="term">bits</span>, and one to 600,000 is <Bits v={-19.2} unit />. Every comparison then adds its own weight, positive
          or negative, to that starting number. Once the total rises above <Bits v={3.2} unit sum />, which is nine chances in ten, the tool treats the pair as
          likely the same person.<Fn n={13} /> This is why a single agreement
          on a common given name, worth a few bits, changes almost nothing,
          and why several independent agreements of about ten bits each change
          everything. It is also why a person entry with no contracts can
          never look certain: it has only the name and the father&rsquo;s name
          to offer, about 21 bits in all, and that leaves it just short of the
          threshold. Both situations are drawn at the end of this section.
        </p>
        <p>
          The tool compares two entries on five things: the name as written;
          the father&rsquo;s and grandfather&rsquo;s names; the years in which
          each entry appears, together with the business partners and firms
          around it; the role in the firm, general partner or external
          investor; and, for a woman, her husband&rsquo;s name. Each comparison
          is scored in the same way, so one is enough to show how. Take the
          father&rsquo;s name. Suppose that among pairs of entries that really
          are the same man the father&rsquo;s name agrees exactly eight times
          in ten, and that among two entries picked at random it agrees, by
          coincidence, six times in a thousand.<Fn n={14} /> The first number
          is called <em>m</em>, the second <em>u</em>. Their ratio, about 130,
          says how much more often true matches agree than strangers do, and
          that ratio is the strength of the evidence. Its logarithm to base
          two, about 7, is the weight in bits. The arithmetic is set out
          below.
        </p>
        <div className="pl-pg-eqn" aria-label={
          "Fathers agree exactly: among true matches about eight in ten, m; " +
          "among random pairs about six in a thousand, u; the ratio is about " +
          "130; its logarithm to base two, about 7, is the weight in bits."
        }>
          <span className="pl-pg-eqn-term"><i>among true matches (<b>m</b>)</i><b>8 in 10</b></span>
          <span className="pl-pg-eqn-op">÷</span>
          <span className="pl-pg-eqn-term"><i>among random pairs (<b>u</b>)</i><b>6 in 1,000</b></span>
          <span className="pl-pg-eqn-op">=</span>
          <span className="pl-pg-eqn-term"><i>ratio</i><b>≈ 130</b></span>
          <span className="pl-pg-eqn-op">→</span>
          <span className="pl-pg-eqn-term is-for"><i>weight, log<sub>2</sub> 130</i><b>+7 bits</b></span>
        </div>
        <p>
          Agreement is not all or nothing. A father written <em>Pagolo</em> in
          one contract and <em>Paolo</em> in the next is rarer among true
          matches than an exact agreement, and rarer still among strangers,
          so it earns a smaller weight but not none. A father who plainly
          differs counts against a match, but by less than one might expect,
          because the scribes contradict one another about real people too: a
          Giovanni di Jacopo Corsi entered twice with two different
          grandfathers turned out, on reading the contract, to be one boy, a
          minor under his uncle&rsquo;s guardianship.<Fn n={15} /> A father the
          scribe did not write down is no evidence either way. In this
          database 43 entries in 100 lack the father&rsquo;s name and 87 in
          100 lack the grandfather&rsquo;s, so that last rule decides more
          comparisons than any other. The strip below shows the four possible
          outcomes for the father&rsquo;s name, with illustrative weights.
        </p>
        <div className="pl-pg-levels" aria-label={
          "The father's name at four outcomes, illustrative weights: fathers " +
          "agree exactly, plus seven; one letter apart, plus four; fathers " +
          "differ, minus one; father not recorded on one side, no evidence."
        }>
          <span className="pl-pg-levels-title" aria-hidden="true">
            The father&rsquo;s name, outcome by outcome (illustrative weights):
          </span>
          <span className="pl-pg-level" aria-hidden="true"><span>fathers agree exactly</span><i className="is-for" style={{ width: "70%" }} /><b>+7</b></span>
          <span className="pl-pg-level" aria-hidden="true"><span>one letter apart, <em>Pagolo</em> and <em>Paolo</em></span><i className="is-for" style={{ width: "40%" }} /><b>+4</b></span>
          <span className="pl-pg-level" aria-hidden="true"><span>fathers differ</span><i className="is-against" style={{ width: "10%" }} /><b>&minus;1</b></span>
          <span className="pl-pg-level is-null" aria-hidden="true"><span>father not recorded on one side</span><i /><b>0</b></span>
        </div>
        <p>
          Weights are logarithms so that they can be added. Each comparison
          contributes its own weight, and every +1 doubles the odds that two
          entries are one person; +10 multiplies the odds about a thousand
          times.<Fn n={16} /> How common a name is enters in the same way:
          strangers seldom share the family name <em>Quaratesi</em> and half
          of Florence shares the given name <em>Giovanni</em>, so <em>u</em>{" "}
          is small for the one and large for the other, and an exact match on
          a name is weighted by how common that name is. The figure below puts
          the prior and the weights on one scale, for two real pairs that the
          next section looks at in detail.
        </p>
        <BitsRuler />
        <p>
          None of these numbers is set by hand. Splink estimates <em>m</em>{" "}
          and <em>u</em> for every outcome from this database itself: <em>u</em>{" "}
          by counting how often the outcome occurs across all 64 million
          possible pairs, <em>m</em> by an iterative fit within the groups of
          entries that share a name (because there is not yet a reviewed set
          of confirmed matches to count from).<Fn n={17} /> The weights in the
          father&rsquo;s-name strip above are illustrative. The fitted weights
          appear in the figure above, on every case, and in the ladder of the
          next section, and they will be re-estimated once the first round of
          reviewed decisions exists.
        </p>
        <p>
          Not every pair of entries is compared. Eleven thousand entries make
          64 million pairs, far too many to score, so the tool first sorts the
          entries into groups by parts of the name and compares only entries
          that fall into the same group. It does this five times, with five
          different groupings: the same given name and family name; the same
          family name and father&rsquo;s name; the same given name and
          father&rsquo;s name; the same family name and grandfather&rsquo;s
          name; and family names one letter apart with the same
          father&rsquo;s name. A pair that shares none of these five is never
          compared at all, however alike the two people may otherwise be.
          <Fn n={18} /> This is why a pair a reader expects may be missing
          from the queue. It is also why the fifth grouping was added: without
          it Salvatore Innori and Salvadore Inori, who share a father and a
          grandfather, had never been compared, because the tool sorts mostly
          by family name and theirs is spelled in two ways.
        </p>
      </section>

      <section className="pl-primer-panel pl-primer-body">
        <h3>One pair, as the tool presents it</h3>
        <p>
          This section follows one real pair in the order in which every case
          is shown: the two entries, their years, the evidence, the decision.
          Andrea di Neri Corsini is entered as entry 5305, with five contracts
          from 1631 to 1644, and as entry 11633, with three contracts from
          1645 to 1657 and a grandfather, Lorenzo, that the first entry
          lacks. Neither entry is part of the labeling round. The weights
          below are the tool&rsquo;s own as fitted today; they will change when
          the model is re-estimated after the first reviewed decisions. The
          table below compares the two entries field by field.
        </p>
        <PairGrid
          rows={CORSINI_ROWS}
          headA="Entry 5305"
          headB="Entry 11633"
          caption="Two person entries, as entered. Firms and partners are summarized here; the case page lists every contract."
          ariaLabel="Entries 5305 and 11633, both Andrea di Neri Corsini, compared field by field: the father agrees, only 11633 gives a grandfather, the careers run 1631 to 1644 and 1645 to 1657, both are external investors in every contract, both appear in Benedetto Nomi e Tommaso Pandolfini e compagni, and seven partners recur."
        />
        <p>
          Read down the table as the tool does. The names agree exactly.
          <em>Andrea</em> is not a rare given name and the Corsini are one of the great
          Florentine families, yet the agreement earns its full weight, <Bits v={10} unit />, because the tool judges rarity on the whole name, and Andrea
          di Neri Corsini is carried by exactly these two entries in the whole
          database. The fathers agree and one grandfather is unrecorded: <Bits v={7.2} unit />, the outcome described in the previous section. Both entries
          are external investors in every contract, a small agreement worth <Bits v={0.7} unit />. Neither has a husband to compare, so that comparison says
          nothing.
        </p>
        <p>
          The fourth comparison carries the most weight. Entry 5305 is last
          seen in 1644; entry 11633 first appears in 1645. An accomandita was
          a partnership for a fixed term that was often renewed, so the same
          people appear together contract after contract: 353 firm names in
          this database span 821 contracts. Here the firm Benedetto Nomi e
          Tommaso Pandolfini e compagni appears on both sides, in 1638 and
          again in 1645, and seven of the partners around it appear beside
          both entries.<Fn n={19} /> Two entries inside one renewed firm a
          few years apart are, far more often than not, one man carrying on
          his business. The tool therefore weighs time and business partners
          as one piece of evidence with several possible outcomes, from
          strongest to weakest: the very same firm; a shared business
          partner; and firm names with only a word or two in common, which
          proves little, because firm names are made of common given names.
          The figure below shows the two entries on one time line, with the
          two contracts of the shared firm marked.
        </p>
        <CorsiniFigure />
        <p>
          The next figure shows the same evidence from the partners&rsquo;
          side: every person who appears in a contract beside either entry.
          The seven who appear beside both stand in the middle; the rest
          belong to one entry only.
        </p>
        <PairCircle data={CORSINI_CIRCLE} />
        <p>
          The ladder below lists the outcomes this comparison can have for
          two entries whose working lives together fit within thirty years,
          as here (1631 to 1657), with the weights as the tool fitted them.
          The rung this pair stands on is marked.
        </p>
        <figure className="pl-ladder">
          <figcaption>Time and business partners, when the two working lives together fit within thirty years (fitted weights):</figcaption>
          <ol>
            <li className="is-chosen"><span>the same firm</span><b>+9.8</b><em>this pair</em></li>
            <li><span>a shared business partner, or firm names with a word or two in common</span><b>+6.5</b></li>
            <li><span>nothing shared</span><b>+0.4</b></li>
            <li className="is-null"><span>no dated contracts on one side, or both entries in the same contract</span><b>0</b></li>
          </ol>
          <p className="pl-ladder-note">
            Entries whose years overlap climb a finer ladder of their own,
            from +1.6 for the overlap alone to +10.1 for the same firm.
            Working lives that only fit within sixty years earn less: +5.6
            with a shared partner or firm, +0.1 without.
          </p>
        </figure>
        <p>
          Two safeguards are built into this comparison. Time and partners are
          weighed together rather than separately, because two men can only
          share a partner while both are alive, so counting the overlap of
          their years and then their shared partner would count one fact
          twice. And a contract that names both entries counts the other way:
          a contract that lists two men side by side is usually distinguishing
          them, so a pair that shares a contract gets no partner evidence at
          all.
        </p>
        <p>
          Now add up: the prior of <Bits v={-19.2} unit />, then <Bits v={10} /> for
          the name, <Bits v={7.2} /> for father and grandfather, <Bits v={9.8} /> for the
          years and the firm, <Bits v={0.7} /> for the role. The total is <Bits v={8.5} unit sum />, odds
          of about 360 to 1, or 997 in 1,000: the climb drawn in{" "}
          <a
            className="pl-primer-jump"
            href="#pl-primer-ruler"
            onClick={(event) => {
              event.preventDefault();
              jumpTo("pl-primer-ruler");
            }}
          >
            the figure of the previous section
          </a>.
        </p>
        <p>
          The chart below is the one the case page draws for every pair. It
          shows the pieces, not the sum: one bar per comparison, pointing
          left for evidence against and right for evidence in favour, without
          the prior and without the numbers, which sit under the heading
          Probability and technical details. The role, which counts for
          little, and the comparisons with nothing to compare are folded into
          a short list beneath. That list is where missing evidence shows,
          and it is worth reading on every case.
        </p>
        <figure className="pl-primer-tool">
          <EvidenceBars rows={CORSINI_EVIDENCE} idPrefix="pl-primer-evidence" />
          <figcaption>Andrea di Neri Corsini, entries 5305 and 11633: the chart as the case page draws it.</figcaption>
        </figure>
        <p>
          The score orders the queue; it is not a verdict. As a ranking it
          works well: the pairs at the top are the ones worth a
          reviewer&rsquo;s time. As a probability it is rough, because the
          weights were estimated without a single confirmed match and the
          prior rests on a guess about how many true matches the strict rule
          finds.<Fn n={20} /> For that reason the probability is shown on a
          case only under its technical details. The Corsini pair itself is
          still undecided, in the queue, waiting for a reviewer.
        </p>
      </section>

      <section className="pl-primer-panel pl-primer-body">
        <h3>Reading the queue</h3>
        <p>
          Before the model scores anything, a few plain rules decide the cases
          they can decide, and those rules sort the pairs into the lanes of
          the list on the left. A person entry with no contracts, but with
          exactly the same given name, father&rsquo;s name and family name as
          an entry that has contracts, and with an entry number assigned in
          the same batch of data entry, is a duplicate created by the typing.
          Two entries with the same full name whose contracts together span
          more than sixty years are two people. And some pairs the rules mark
          as needing a reading of the contract itself: two entries with the
          same name in one contract, two entries whose grandfathers contradict
          each other, or a pair in which one entry&rsquo;s heirs appear in a
          contract before the other entry is last seen alive. For those the
          tool decides nothing. Everything the rules leave open, the model
          ranks.<Fn n={21} />
        </p>
        <figure className="pl-lanes-table">
          <dl>
            <div>
              <dt>Labeling round</dt>
              <dd>150 cases chosen to cover every kind of evidence, judged with the scores hidden. The model will be re-estimated on these decisions.</dd>
            </div>
            <div>
              <dt>Likely duplicates</dt>
              <dd>The duplicates created by the typing, and pairs with the same full name and a plausible combined working life. Confirm them one by one.</dd>
            </div>
            <div>
              <dt>High-concordance variants</dt>
              <dd>Names that differ slightly while everything else agrees. These need a human eye; the tool never claims a match here on its own.</dd>
            </div>
            <div>
              <dt>Read the source</dt>
              <dd>The pairs marked as needing a reading of the contract. The contract itself decides.</dd>
            </div>
            <div>
              <dt>Other possible matches</dt>
              <dd>Every pair the model ranks, best first. The filter called Stranded entries collects the pairs in which one entry has no contracts.</dd>
            </div>
            <div>
              <dt>Possible combined identities</dt>
              <dd>Entries that may hold two people. They can be flagged, not split.</dd>
            </div>
            <div>
              <dt>Decided</dt>
              <dd>What has been answered, with its history and an undo.</dd>
            </div>
            <div>
              <dt>Rule-based exclusions</dt>
              <dd>Pairs the sixty-year rule refuses. Check a sample and approve the rule, rather than each pair.</dd>
            </div>
          </dl>
          <figcaption>The lanes of the list on the left, in the same order, and what to do in each.</figcaption>
        </figure>
      </section>

      <section className="pl-primer-panel pl-primer-body">
        <h3>Four ways to answer</h3>
        <p>
          Under every case sits the same question, do these entries describe
          the same historical person, and the same four answers.
        </p>
        <div className="pl-primer-dock" aria-hidden="true">
          <div className="pl-decision-bar">
            <div className="pl-decision-question">
              <span>Your decision</span>
              <strong>Do these entries describe the same historical person?</strong>
              <small>The entries themselves stay unchanged · every decision can be undone</small>
            </div>
            <div className="pl-decision-action-area">
              <div className="pl-decision-options">
                <button className="is-same" type="button" tabIndex={-1}>
                  <span>✓</span>Same person
                </button>
                <button className="is-distinct" type="button" tabIndex={-1}>
                  <span>≠</span>Different people
                </button>
                <button className="is-uncertain" type="button" tabIndex={-1}>
                  <span>?</span>Not enough evidence
                </button>
              </div>
              <button className="pl-split-secondary" type="button" tabIndex={-1}>
                Flag one entry as possibly containing multiple people…
              </button>
            </div>
          </div>
        </div>
        <ul className="pl-pg-answers">
          <li>
            <strong>Same person.</strong> From now on the two entries count as
            one person. Both stay exactly as they were entered.
          </li>
          <li>
            <strong>Different people.</strong> The pair is settled and leaves
            the queue.
          </li>
          <li>
            <strong>Not enough evidence.</strong> The case is set aside, with
            a reason, until someone reopens it.
          </li>
          <li>
            <strong>Flag one entry as possibly containing multiple people.</strong>{" "}
            Marks a single entry that may hold two people, like the
            Guicciardini entry of the second section. Nothing is split.
          </li>
        </ul>
        <p className="pl-primer-note">
          Every answer is recorded under your initials and can be undone. In
          the labeling round the scores stay hidden until you have answered.
          <Fn n={22} />
        </p>
      </section>

      <Notes notes={PRIMER_NOTES} />
    </article>
  );
}
