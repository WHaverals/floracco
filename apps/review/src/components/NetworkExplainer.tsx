import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { NetworkCircle, NetworkFirm } from "../types";
import FirmFigure, { FirmKey } from "./FirmFigure";
import NetworkFigure from "./NetworkFigure";

/* "How to read a circle", the Network landing's explainer (the plan in
 * docs/network/landing_explainer_plan_2026-11-02.md, the build contract in
 * docs/network/explainer_contract.md). The text is the mockup's
 * <section id="explainer">, docs/network/mockups/landing_explainer_2026-11-02.html,
 * full length, as WH decided on 2 November 2026. Shown at the foot of the
 * landing, and with `explain=1` in a circle's figure column. One running
 * example, the Florentine silk firm Ferdinando Morelli e compagni, registered
 * as acts 3880 (1786), 4419 (1795), 4666 (1801) and 4849 (1803).
 *
 * Every fact was verified read-only against main.db on 2 November 2026. The
 * two figures draw responses frozen that day, and the chain is a third
 * (content/network-explainer/, each with its request and date; the two the
 * figures draw are imported on demand, in their own chunks;
 * scripts/refresh_network_explainer.py compares them with main.db; a test in
 * tests/test_network.py checks the text against them):
 * - entry 8185, Ferdinando di Pietro Morelli, is general partner on 3880 and
 *   4419 and on no other act; the eight entries named with him there are
 *   3102, 3283, 8188, 3104, 8031, 8189, 9389 and 9411 (circle-8185.json);
 * - Lorenzo Vannuccini 3102 and Giovanni Battista Altoviti 3283 share five
 *   acts in the circle, 1540, 1624, 3880, 4409 and 4419 (1771 to 1795), the
 *   widest line;
 * - 3102 is general partner on 1540 (1771, Giuseppe Martini e compagni), 1624
 *   (1774, Giuseppe Martire e compagni) and 4409 (1794, Giuseppe Martire e
 *   compagno) and external investor on 3880 and 4419; coloured by role, 8185
 *   is general partner, 3102 both, the other seven external investors;
 * - between the firm's registrations 4 stayed, 3 left and 2 joined (1786 to
 *   1795), 1, 5 and 6 (1795 to 1801), 4, 3 and 5 (1801 to 1803); 8185 is on
 *   4419 and not on 4666; 9424, Ferdinando Morelli with no father's name, is
 *   general partner on 4666 and 4849 (firm-ferdinando-morelli.json); the rows
 *   are drawn as stored, Altoviti 3283 as general partner on 4849 included;
 * - 4666 began on 1 July 1800 and was registered on 18 December 1801: the
 *   start date by `SELECT start_date FROM contract WHERE contract_id = 4666` →
 *   1800-07-01, the registration date in the firm file;
 * - Gregorio Benucci 8189 stands only on 3880 and fades under the window
 *   1795–1803; Vincenzio Sestini 10269 stands only on 4753 (1805-11-21,
 *   fratelli Stefanini e compagni); the chain 8189 → 3283 → 10269 runs forward
 *   in time over two acts, 3880 and 4753 (chain-8189-10269.json).
 * None of the entries drawn or named here (the circle's nine, the firm's
 * twenty, and 10269) is in the open Judge-blind packet labeling_packet_v3.jsonl,
 * as checked on 2 November 2026. Mario Morelli e compagni (5308 and 6844) is an
 * open case of that round and stays off this page. 8185 and 9424 are
 * described only as two entries with one name; the pair 9411 and 10059, both
 * Cosimo Paradisi, a People candidate, is drawn in the firm figure as the live
 * firm view draws it and is not named in the text. */

/** The date the figures' responses were frozen, at the foot of each caption. */
const DATA_OF = "Data of 2 November 2026.";
/** Figure A's frame: the text measure's width, this tall. */
const CIRCLE_HEIGHT = 360;
/** The id the landing's "How to read a circle" scrolls to. */
export const EXPLAINER_ID = "nw-explainer";

type Figures = { circle: NetworkCircle; firm: NetworkFirm };

/** The figures' frozen responses, kept once loaded, so the landing and a
 * circle's explain pane draw them at once on every later visit. */
let figuresLoaded: Figures | null = null;
let figuresLoading: Promise<Figures> | null = null;

/** The two frozen responses, fetched once per page load; a failed fetch can be tried again. */
function loadFigures(): Promise<Figures> {
  if (!figuresLoading) {
    figuresLoading = Promise.all([
      import("../content/network-explainer/circle-8185.json"),
      import("../content/network-explainer/firm-ferdinando-morelli.json"),
    ]).then(
      ([circle, firm]) => {
        figuresLoaded = { circle: circle.default as unknown as NetworkCircle, firm: firm.default as unknown as NetworkFirm };
        return figuresLoaded;
      },
      (e: Error) => {
        figuresLoading = null;
        throw e;
      },
    );
  }
  return figuresLoading;
}

function useFigures(): { figures: Figures | null; error: string; retry: () => void } {
  const [figures, setFigures] = useState<Figures | null>(figuresLoaded);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (figures) return;
    let live = true;
    setError("");
    loadFigures()
      .then((data) => live && setFigures(data))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [figures, attempt]);
  return { figures, error, retry: () => setAttempt((n) => n + 1) };
}

/** A figure's place while its data load: the frame's height when known, or the
 * failure with "Try again", as the landing's own load reads. */
function FigureWait({ error, retry, height }: { error: string; retry: () => void; height?: number }) {
  if (error) {
    return (
      <p className="error-text nw-explainer-wait" style={height ? { height } : undefined}>
        {error}{" "}
        <button type="button" className="link-like" onClick={retry}>
          Try again
        </button>
      </p>
    );
  }
  return (
    <p className="muted nw-explainer-wait" style={height ? { height } : undefined}>
      Loading…
    </p>
  );
}

export default function NetworkExplainer() {
  const { figures, error, retry } = useFigures();
  return (
    <section className="nw-explainer" id={EXPLAINER_ID} aria-labelledby="nw-explainer-heading">
      <h2 id="nw-explainer-heading">How to read a circle</h2>
      <p>
        An <span className="term">act</span>, on these pages, is the registration of one accomandita, a limited partnership,
        as entered from the registers at the Archivio di Stato in Florence. It names the partners in two
        roles: <span className="term">general partners</span>, who managed the firm and were liable for all its debt, and{" "}
        <span className="term">external investors (accomandanti)</span>, who put in a set sum and could lose only that. Each
        name on an act is a <span className="term">person entry</span>, as it was entered; a name spelled another way, or
        written without the father's name, could start a new entry, so one person may stand as several.
        The examples come from one Florentine silk firm, <i>Ferdinando Morelli e compagni</i>,
        registered four times between 1786 and 1803.
      </p>

      <h3>With whom, and how often?</h3>
      <p>
        A circle starts from one entry. A line joins two entries named on the same act and widens
        with every act they share. <Link to="/network/person/8185?steps=1">Ferdinando di Pietro Morelli, entry 8185</Link>,
        is general partner on the firm's acts of 1786 and 1795 and on no other; his circle holds the
        eight entries named with him there, clockwise from the top in the order of their first act with
        him. The widest line does not touch him: Lorenzo Vannuccini and Giovanni Battista Altoviti stand
        together on five acts, from 1771 to 1795.
      </p>
      <figure className="nw-explainer-figure">
        {figures ? (
          <NetworkFigure data={figures.circle} hubs="stop" selected={null} highlight={null} colour="role" labelAll={false} still height={CIRCLE_HEIGHT} />
        ) : (
          <FigureWait error={error} retry={retry} height={CIRCLE_HEIGHT} />
        )}
        <figcaption>
          The circle of entry 8185, coloured by role.
          <span className="nw-explainer-key">
            <span>
              <i className="nw-key-dot is-role-gp" /> general partner
            </span>
            <span>
              <i className="nw-key-dot is-role-lp" /> external investor (accomandante)
            </span>
            <span>
              <i className="nw-key-dot is-role-both" /> both, on different acts
            </span>
          </span>
          <span className="nw-explainer-data">{DATA_OF}</span>
        </figcaption>
      </figure>
      <p>
        A line records an act signed together and says nothing of kinship or friendship; a missing
        line means only that no registered act names both entries. An entry on more than 20 acts is
        drawn with a ring, and the second step stops at it unless set to go through; the cut is for
        readability.
      </p>

      <h3>Who managed the firm, and who put in money?</h3>
      <p>
        The role is read from each entry's stake on each act.{" "}
        <Link to="/network/person/8185?steps=1&colour=role">Coloured by role</Link>, the circle shows entry
        8185 as general partner, seven of his co-signers as external investors, and Vannuccini in a third
        colour: an investor in this firm, he is general partner on three acts of 1771, 1774 and 1794 in
        the same circle. The colour reads only the acts in the circle, so an entry can
        take another colour in another circle.
      </p>

      <h3>Did the firm keep its partners?</h3>
      <p>
        An accomandita ran for a fixed term and was often formed again, each time as a new
        registered act naming its partners again. A termination, variation or renewal filed against a
        registration lists no partners, so it draws no line.{" "}
        <Link to="/network/person/8185?steps=1&firm=Ferdinando+Morelli+e+compagni">The firm view</Link> sets
        the registrations of one firm name side by side, with those later acts as ticks, and counts from
        each to the next the entries that stayed, left and joined.
      </p>
      <figure className="nw-explainer-figure is-firm">
        {figures ? <FirmFigure data={figures.firm} selected={null} highlight={null} still /> : <FigureWait error={error} retry={retry} />}
        <figcaption>
          <i>Ferdinando Morelli e compagni</i>: its four registrations as columns, the twenty
          person entries named on them as rows, as entered. {DATA_OF}
          {figures && <FirmKey data={figures.firm} />}
        </figcaption>
      </figure>
      <p>
        From 1795 to 1801 one entry stayed, five left and six joined: entry 8185 among those who
        left, and a Ferdinando Morelli entered without a father's name, entry 9424, among those who
        joined. Close spellings of one name, and a family's stake entered under one of its members,
        count as different entries, so a count of those who left is worth checking against the
        acts. The firm name is followed as entered too: Vannuccini's three acts as
        general partner stand under <i>Giuseppe Martini e compagni</i>, <i>Giuseppe Martire e
        compagni</i> and <i>Giuseppe Martire e compagno</i>.
      </p>

      <h3>When, and how far apart?</h3>
      <p>
        Acts are dated by registration, which could come well after the partnership
        began: the firm's partnership of 1 July 1800 was registered on 18 December 1801. Under a window
        on the time strip only the acts registered inside it make lines; an entry with no act inside
        fades in place, as Gregorio Benucci, named only in 1786, does under{" "}
        <Link to="/network/person/8185?steps=1&years=1795-1803">a window from 1795 to 1803</Link>.
      </p>
      <p>
        A <span className="term">chain</span> is the fewest acts leading from one entry to another.{" "}
        <Link to="/network/person/8189?to=10269">Gregorio Benucci, entry 8189, and Vincenzio Sestini, entry 10269</Link>,
        named on an act of <i>fratelli Stefanini e compagni</i> in 1805, are two acts apart through
        Giovanni Battista Altoviti, and the chain runs forward in time: each act is registered no earlier
        than the one before. A chain measures distance in the documents; it settles neither acquaintance
        nor identity.
      </p>

      <p className="nw-explainer-closing">
        Every act on the drawing opens with its Word summary; the names and roles
        drawn are the rows typed from it, and where they disagree, the summary is the one to read.
        Whether two entries describe one person is decided in People; its introduction,{" "}
        <Link to="/people">One person, several entries</Link>, explains how.
      </p>
    </section>
  );
}
