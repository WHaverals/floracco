import type { DbFacets } from "../types";

/** A by-decade histogram with a dual-thumb range slider beneath it. The bars are
 *  the corpus's date distribution (orientation); the two thumbs set the year span.
 *  Bars inside the selected span are highlighted. */
function DateRangeSlider({
  facets,
  range,
  onChange,
}: {
  facets: DbFacets;
  range: [number, number] | null;
  onChange: (next: [number, number] | null) => void;
}) {
  const min = facets.year_min;
  const max = facets.year_max;
  if (min == null || max == null || max <= min) return null;
  const lo = range ? range[0] : min;
  const hi = range ? range[1] : max;
  const maxCount = Math.max(1, ...facets.year_histogram.map((h) => h.count));

  // Clamp the two thumbs so they never cross, and collapse a full-span selection
  // back to "no filter" (null) so it neither sends params nor shows a chip.
  const apply = (nextLo: number, nextHi: number) => {
    const a = Math.min(nextLo, nextHi);
    const b = Math.max(nextLo, nextHi);
    onChange(a <= min && b >= max ? null : [a, b]);
  };

  return (
    <div className="db-date-filter">
      <div className="db-date-head">
        <span>Years</span>
        <span className="db-date-range">
          {lo}–{hi}
        </span>
      </div>
      <div className="db-histogram" aria-hidden="true">
        {facets.year_histogram.map((h) => {
          const inRange = h.decade + 9 >= lo && h.decade <= hi;
          return (
            <span
              key={h.decade}
              className={inRange ? "db-bar is-in" : "db-bar"}
              style={{ height: `${Math.max(6, Math.round((h.count / maxCount) * 100))}%` }}
              title={`${h.decade}s · ${h.count}`}
            />
          );
        })}
      </div>
      <div className="db-range">
        <input
          type="range"
          min={min}
          max={max}
          value={lo}
          aria-label="From year"
          onChange={(e) => apply(Number(e.target.value), hi)}
        />
        <input
          type="range"
          min={min}
          max={max}
          value={hi}
          aria-label="To year"
          onChange={(e) => apply(lo, Number(e.target.value))}
        />
      </div>
    </div>
  );
}

export default function DbFilters({
  facets,
  register,
  onRegister,
  yearRange,
  onYearRange,
  subType,
  onSubType,
  showTypes,
  gender,
  onGender,
}: {
  facets: DbFacets;
  register: string;
  onRegister: (folder: string) => void;
  yearRange: [number, number] | null;
  onYearRange: (next: [number, number] | null) => void;
  subType: string;
  onSubType: (value: string) => void;
  showTypes: boolean;
  gender: string;
  onGender: (value: string) => void;
}) {
  // People carry no register/date — they get the gender facet on its own.
  if (facets.genders.length > 0 && facets.registers.length === 0) {
    return (
      <div className="db-filters">
        <div className="db-filter-field">
          <span className="db-filter-label">Recorded gender</span>
          <div className="db-type-pills">
            {facets.genders.map((g) => (
              <button
                key={g.value}
                type="button"
                className={gender === g.value ? "db-type-pill is-active" : "db-type-pill"}
                onClick={() => onGender(gender === g.value ? "" : g.value)}
              >
                {g.label} <span className="db-type-count">{g.count.toLocaleString()}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="db-filters">
      <label className="db-filter-field">
        <span className="db-filter-label">Register</span>
        <select value={register} onChange={(e) => onRegister(e.target.value)}>
          <option value="">All registers</option>
          {facets.registers.map((r) => (
            <option key={r.folder || "(none)"} value={r.folder}>
              {r.label} ({r.count.toLocaleString()})
            </option>
          ))}
        </select>
      </label>

      <DateRangeSlider facets={facets} range={yearRange} onChange={onYearRange} />

      {showTypes && facets.sub_types.length > 0 && (
        <div className="db-filter-field">
          <span className="db-filter-label">Type</span>
          <div className="db-type-pills">
            {facets.sub_types.map((t) => (
              <button
                key={t.value}
                type="button"
                className={subType === t.value ? "db-type-pill is-active" : "db-type-pill"}
                onClick={() => onSubType(subType === t.value ? "" : t.value)}
              >
                {t.value} <span className="db-type-count">{t.count.toLocaleString()}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
