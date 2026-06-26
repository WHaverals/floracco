import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { geoMercator, geoPath } from "d3-geo";
import { feature } from "topojson-client";
import type { FeatureCollection } from "geojson";
import topo from "../geo/countries-50m.json";
import { loadPlaceMap } from "../api";
import type { PlaceMapPoint } from "../types";

/* The place vocabulary as geography. Quiet editorial cartography — real country
 * outlines (bundled Natural Earth, no tiles) on warm cream, monochrome warm dots
 * sized by usage. Approximate coordinates (parish→parent city, realm→capital,
 * country centroid) render as hollow dashed rings, so the map never implies a
 * precision the machine resolution doesn't have. */

const W = 920;
const PAD = 14;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const LAND = feature(topo as any, (topo as any).objects.countries) as unknown as FeatureCollection;

// One frame: Europe & the Mediterranean. Zoom in (incl. all the way into Tuscany)
// rather than a separate preset. Fit to the two opposite corners as points — a
// spherical Polygon's winding is orientation-sensitive in d3-geo (a CCW ring reads
// as "the whole world minus the box"), silently leaving the projection at world scale.
const EUROPE_BBOX: GeoJSON.MultiPoint = {
  type: "MultiPoint",
  coordinates: [[-11, 29], [40, 55]],
};

export default function PlaceMap({ onPick }: { onPick: (p: PlaceMapPoint) => void }) {
  const [points, setPoints] = useState<PlaceMapPoint[] | null>(null);
  const [available, setAvailable] = useState(true);
  const [hover, setHover] = useState<{ p: PlaceMapPoint; x: number; y: number } | null>(null);
  const [query, setQuery] = useState("");
  const highlight = hover?.p.id ?? null;   // shared map↔list highlight, driven by hover
  const [tf, setTf] = useState({ k: 1, x: 0, y: 0 });   // pan/zoom transform
  const [frameW, setFrameW] = useState(920);            // rendered map width (for legend scale)
  const frameRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const gRef = useRef<SVGGElement>(null);
  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean } | null>(null);
  const liveXY = useRef({ x: 0, y: 0 });
  const justDragged = useRef(false);

  useEffect(() => {
    loadPlaceMap()
      .then((res) => {
        setAvailable(res.available);
        setPoints(res.points);
      })
      .catch(() => setAvailable(false));
  }, []);

  // track the map's rendered width: the SVG scales to fit (viewBox 920 → container),
  // so the legend must scale dots by the same factor to match what's on the map.
  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) setFrameW(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [available]);

  // wheel zoom around the cursor (native, non-passive so preventDefault works)
  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const sx = ((e.clientX - rect.left) / rect.width) * W;
      const sy = ((e.clientY - rect.top) / rect.height) * vhRef.current;
      setTf((t) => {
        const k = Math.min(40, Math.max(1, t.k * (e.deltaY < 0 ? 1.18 : 1 / 1.18)));
        const f = k / t.k;
        let x = sx - (sx - t.x) * f;
        let y = sy - (sy - t.y) * f;
        if (k === 1) { x = 0; y = 0; }
        return { k, x, y };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // Pan via WINDOW listeners (not pointer capture): capture re-targets the
  // following `click` to the frame, so a dot's onClick never fires. Window
  // listeners let the drag track outside the frame AND leave dot clicks intact.
  const onPointerDown = (e: ReactPointerEvent) => {
    if (e.button !== 0) return;
    const start = { x: e.clientX, y: e.clientY, tx: tf.x, ty: tf.y, moved: false };
    drag.current = start;
    const k = tf.k;
    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - start.x;
      const dy = ev.clientY - start.y;
      if (!start.moved && Math.hypot(dx, dy) < 8) return;   // generous: a drifty click still selects
      start.moved = true;
      setHover(null);                                         // a real drag started — drop the tooltip/halo
      const frame = frameRef.current;
      if (!frame) return;
      const scale = W / frame.getBoundingClientRect().width;
      liveXY.current = { x: start.tx + dx * scale, y: start.ty + dy * scale };
      // pan imperatively so a ~650-dot map doesn't re-render on every move
      gRef.current?.setAttribute("transform", `translate(${liveXY.current.x},${liveXY.current.y}) scale(${k})`);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      drag.current = null;
      if (start.moved) {
        justDragged.current = true;                      // suppress the click that ends a drag
        setTimeout(() => { justDragged.current = false; }, 0);
        setTf((t) => ({ ...t, x: liveXY.current.x, y: liveXY.current.y }));
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const pick = (p: PlaceMapPoint) => { if (!justDragged.current) onPick(p); };

  const { landPath, projected, vh } = useMemo(() => {
    const bbox = EUROPE_BBOX;
    // derive the viewBox height from the frame's projected aspect, so the map
    // fills the panel with no letterbox bands top/bottom.
    const measure = geoMercator().fitWidth(W - 2 * PAD, bbox);
    const mb = geoPath(measure).bounds(bbox);
    const h = Math.max(220, Math.round(mb[1][1] - mb[0][1] + 2 * PAD));
    const projection = geoMercator().fitExtent([[PAD, PAD], [W - PAD, h - PAD]], bbox);
    const path = geoPath(projection);
    const land = LAND.features.map((f) => path(f) ?? "").filter(Boolean);
    const pts = (points ?? [])
      .map((p) => {
        const xy = projection([p.lon, p.lat]);
        return xy ? { p, x: xy[0], y: xy[1] } : null;
      })
      .filter((d): d is { p: PlaceMapPoint; x: number; y: number } =>
        !!d && d.x >= 0 && d.x <= W && d.y >= 0 && d.y <= h)
      // draw biggest last so small dots don't hide under them on hover
      .sort((a, b) => a.p.count - b.p.count);
    return { landPath: land, projected: pts, vh: h };
  }, [points]);

  const vhRef = useRef(560);
  vhRef.current = vh;

  // The counts are steeply power-law (0–4,206; ~95% of places ≤ 20). AREA ∝ count
  // is "honest" for ratios but collapses that tail into one indistinguishable dot,
  // so the map can't show the distribution. We size by LOG(count) instead — each
  // order of magnitude (1 → 10 → 100 → 1,000) gets a visible step, the way a log
  // axis honestly renders power-law data. ~one extra radius step ≈ ×10 the contracts;
  // the legend spells out the scale and the tooltip gives the exact count.
  const R_MIN = 2.5;
  const R_MAX = 17;
  const maxCount = useMemo(() => Math.max(1, ...projected.map((d) => d.p.count)), [projected]);
  const radius = (count: number) => {
    const t = Math.log(count + 1) / Math.log(maxCount + 1);   // 0..1
    return R_MIN + (R_MAX - R_MIN) * t;
  };

  // when a place is highlighted from the map, scroll its list row into view
  useEffect(() => {
    if (highlight == null || !listRef.current) return;
    listRef.current
      .querySelector(`[data-pid="${highlight}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [highlight]);

  const listItems = useMemo(() => {
    const q = query.trim().toLowerCase();
    return projected
      .map((d) => d.p)
      .filter((p) => !q || p.value.toLowerCase().includes(q))
      .sort((a, b) => b.count - a.count);
  }, [projected, query]);

  const xyById = useMemo(
    () => new Map(projected.map((d) => [d.p.id, { x: d.x, y: d.y }])),
    [projected],
  );

  // Places stacked under the hovered dot (co-located within the dot's on-screen
  // radius — e.g. a city + its realm + a fair all geocoded to the same point).
  // Threshold shrinks with zoom, so deep-zoomed near-neighbours un-stack.
  const hoverStack: PlaceMapPoint[] = hover
    ? projected
        .filter((d) => Math.hypot(d.x - hover.x, d.y - hover.y) <= (radius(hover.p.count) + 2) / tf.k)
        .map((d) => d.p)
        .sort((a, b) => b.count - a.count)
    : [];

  if (!available) {
    return (
      <div className="ref-overview">
        <p className="eyebrow">Map</p>
        <h2>Places on the map</h2>
        <p className="muted ref-note">
          No coordinates yet. Run the historic-name resolution batch
          (<code>workflows/place_resolve.py</code>) to place every term on the map.
        </p>
      </div>
    );
  }

  const shown = projected.length;
  return (
    <div className="pm-wrap">
      <div className="pm-head">
        <div>
          <p className="eyebrow">Map</p>
          <h2>Where the partners traded</h2>
        </div>
        {tf.k > 1 && (
          <div className="db-tabs pm-views">
            <button type="button" className="db-tab" onClick={() => setTf({ k: 1, x: 0, y: 0 })}>
              Reset zoom
            </button>
          </div>
        )}
      </div>
      <p className="muted ref-note pm-caption">
        {points ? `${shown.toLocaleString()} places in view` : "Loading…"} · dot size = contracts ·
        hollow ring = approximate · scroll to zoom, drag to pan
      </p>

      <div className="pm-body">
        <div
          className="pm-frame"
          ref={frameRef}
          onMouseLeave={() => setHover(null)}
          onPointerDown={onPointerDown}
        >
          <svg viewBox={`0 0 ${W} ${vh}`} width="100%" role="img" aria-label={`Map of contract places across Europe and the Mediterranean`}>
            <g ref={gRef} transform={`translate(${tf.x},${tf.y}) scale(${tf.k})`}>
              <g>
                {landPath.map((d, i) => (
                  <path key={i} d={d} fill="#eadcc6" stroke="#d8c7ac" strokeWidth={0.5 / tf.k} />
                ))}
              </g>
              <g style={{ pointerEvents: "none" }}>
                {projected.map(({ p, x, y }) =>
                  p.approx ? (
                    <circle
                      key={p.id}
                      cx={x}
                      cy={y}
                      r={Math.max(6, radius(p.count)) / tf.k}
                      fill="transparent"
                      stroke="#b07a47"
                      strokeWidth={1.3 / tf.k}
                      strokeDasharray={`${3 / tf.k} ${3 / tf.k}`}
                    />
                  ) : (
                    <circle
                      key={p.id}
                      cx={x}
                      cy={y}
                      r={radius(p.count) / tf.k}
                      fill="#7a4a26"
                      fillOpacity={0.82}
                      stroke="#f6efe3"
                      strokeWidth={0.8 / tf.k}
                    />
                  ),
                )}
              </g>
              {/* transparent hit targets — min ~8px so even a 1-contract dot is easy to click */}
              <g>
                {projected.map(({ p, x, y }) => (
                  <circle
                    key={p.id}
                    cx={x}
                    cy={y}
                    r={Math.max(8, radius(p.count) + 3) / tf.k}
                    fill="transparent"
                    className="pm-dot"
                    onMouseEnter={() => { if (!drag.current) setHover({ p, x, y }); }}
                    onClick={() => pick(p)}
                  />
                ))}
              </g>
              {hover && (
                // selection outline — hugs the dot (so it doesn't read as a bigger
                // dot / change the size encoding), but min ~8px so it still surfaces a
                // dot buried under a bigger one. Also the click target for the haloed place.
                <circle
                  cx={hover.x}
                  cy={hover.y}
                  r={(Math.max(8, radius(hover.p.count) + 2.5)) / tf.k}
                  fill="transparent"
                  stroke="#2e2620"
                  strokeWidth={1.4 / tf.k}
                  className="pm-dot"
                  onMouseEnter={() => { if (!drag.current) setHover({ p: hover.p, x: hover.x, y: hover.y }); }}
                  onClick={() => pick(hover.p)}
                />
              )}
            </g>
          </svg>

          {hover && (
            <div
              className={hoverStack.length > 1 ? "pm-tip pm-tip-stack" : "pm-tip"}
              style={{
                left: `${((hover.x * tf.k + tf.x) / W) * 100}%`,
                top: `${((hover.y * tf.k + tf.y) / vh) * 100}%`,
              }}
            >
              {hoverStack.length > 1 ? (
                <>
                  <span className="pm-tip-name">{hoverStack.length} places here</span>
                  <div className="pm-tip-rows">
                    {hoverStack.map((p) => (
                      <button key={p.id} type="button" className="pm-tip-row" onClick={() => pick(p)}>
                        <span className="pm-tip-row-name">{p.value}</span>
                        <span className="pm-tip-row-meta">
                          {p.count > 0 ? `${p.count.toLocaleString()}×` : p.approx ? "approx" : "—"}
                        </span>
                      </button>
                    ))}
                  </div>
                </>
              ) : (
                <>
                  <span className="pm-tip-name">{hover.p.value}</span>
                  <span className="pm-tip-meta">
                    {hover.p.modern_name && hover.p.modern_name !== hover.p.value ? `${hover.p.modern_name} · ` : ""}
                    {hover.p.type}
                    {hover.p.approx ? " · approx." : ""}
                  </span>
                  <span className="pm-tip-meta">
                    {hover.p.count > 0 ? `${hover.p.count.toLocaleString()} contract${hover.p.count === 1 ? "" : "s"}` : "unused"}
                  </span>
                </>
              )}
            </div>
          )}
        </div>

        <div className="pm-list">
          <input
            className="db-search pm-list-search"
            type="search"
            placeholder={`Search ${shown.toLocaleString()} places…`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <ul className="pm-list-items" ref={listRef} onMouseLeave={() => setHover(null)}>
            {listItems.map((p) => (
              <li key={p.id} data-pid={p.id}>
                <button
                  type="button"
                  className={p.id === highlight ? "pm-row is-active" : "pm-row"}
                  onMouseEnter={() => {
                    const xy = xyById.get(p.id);
                    if (xy) setHover({ p, x: xy.x, y: xy.y });
                  }}
                  onClick={() => onPick(p)}
                >
                  <span className="pm-row-name">{p.value}</span>
                  <span className="pm-row-meta muted">
                    {p.count > 0 ? `${p.count.toLocaleString()}×` : p.approx ? "approx" : "—"}
                  </span>
                </button>
              </li>
            ))}
            {listItems.length === 0 && <li className="db-empty muted">No places match.</li>}
          </ul>
        </div>
      </div>

      <div className="pm-legend">
        <span><span className="pm-swatch pm-swatch-solid" />settlement (city · town · village)</span>
        <span><span className="pm-swatch pm-swatch-ring" />region · realm · parish (approximate)</span>
        {/* HTML circles sized in CSS px at the map's current scale, so a legend dot
            is exactly the size of the same-count dot on the map. Log scale → each
            ×10 in contracts is one step up. */}
        <span className="pm-size-legend">
          {[1, 10, 100, 1000, 4000].map((c) => {
            const d = Math.round(2 * radius(c) * (frameW / W));   // diameter, map scale
            return (
              <span key={c} className="pm-size-item">
                <span className="pm-size-dot" style={{ width: d, height: d }} />
                <span className="pm-size-label">{c.toLocaleString()}</span>
              </span>
            );
          })}
          <span className="muted pm-size-cap">contracts (log scale)</span>
        </span>
      </div>
    </div>
  );
}
