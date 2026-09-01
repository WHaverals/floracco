import type { PersonLinkagePerson } from "../../types";

function yearOf(value: string | null): number | null {
  if (!value || value === "0000-00-00") return null;
  const year = Number(String(value).slice(0, 4));
  return Number.isFinite(year) && year > 0 ? year : null;
}

export default function CareerSpanRibbon({ persons }: { persons: PersonLinkagePerson[] }) {
  const rows = persons.map((person) => {
    const livingByYear = new Map<number, PersonLinkagePerson["appearances"]>();
    const posthumousByYear = new Map<number, PersonLinkagePerson["appearances"]>();
    person.appearances.forEach((appearance) => {
      const year = yearOf(appearance.registration_date);
      if (year === null) return;
      const target = appearance.heirs_of ? posthumousByYear : livingByYear;
      target.set(year, [...(target.get(year) ?? []), appearance]);
    });
    const living = [...livingByYear.keys()].sort((left, right) => left - right);
    const posthumous = [...posthumousByYear.keys()].sort((left, right) => left - right);
    const undated = person.appearances.filter((appearance) => yearOf(appearance.registration_date) === null).length;
    return {
      person,
      livingFirst: living.length ? Math.min(...living) : null,
      livingLast: living.length ? Math.max(...living) : null,
      living,
      livingByYear,
      posthumous,
      posthumousByYear,
      undated,
    };
  });
  const datedYears = rows.flatMap((row) => [
    ...(row.livingFirst !== null ? [row.livingFirst] : []),
    ...(row.livingLast !== null ? [row.livingLast] : []),
    ...row.posthumous,
  ]);
  if (datedYears.length === 0) {
    return <p className="muted">No dated living or posthumous appearances are available.</p>;
  }
  const first = Math.min(...datedYears);
  const last = Math.max(...datedYears);
  const groupSpan = last - first;
  const targetSpan = Math.max(60, Math.ceil((groupSpan + 10) / 10) * 10);
  const midpoint = (first + last) / 2;
  let axisStart = Math.floor((midpoint - targetSpan / 2) / 10) * 10;
  let axisEnd = axisStart + targetSpan;
  // Snapping the start down to a decade can leave the centred window a few
  // years short of the data; widen to the next decade so no point plots
  // outside the axis.
  if (axisEnd < last) axisEnd = Math.ceil(last / 10) * 10;
  if (axisStart > first) axisStart = Math.floor(first / 10) * 10;
  const axisSpan = axisEnd - axisStart;
  const ticks = Array.from(
    { length: Math.floor(axisSpan / 10) + 1 },
    (_, index) => axisStart + index * 10,
  );
  const W = 760;
  const labelW = 165;
  const plotW = W - labelW - 18;
  const rowH = 42;
  const H = rows.length * rowH + 34;
  const x = (year: number) => labelW + ((year - axisStart) / axisSpan) * plotW;
  const livingRows = rows.filter(
    (row) => row.livingFirst !== null && row.livingLast !== null,
  );
  let relationship = `${groupSpan}-year span across all dated appearances`;
  if (livingRows.length === 2) {
    const [left, right] = livingRows;
    const sharedYears = left.living.filter((year) => right.living.includes(year));
    if (sharedYears.length) {
      const sameContract = sharedYears.some((year) => {
        const leftContracts = new Set(
          (left.livingByYear.get(year) ?? []).map((appearance) => appearance.contract_id),
        );
        return (right.livingByYear.get(year) ?? []).some(
          (appearance) => leftContracts.has(appearance.contract_id),
        );
      });
      relationship = sharedYears.length === 1
        ? `Both records have appearances dated ${sharedYears[0]} · ${
          sameContract ? "same contract" : "different contracts"
        }`
        : `Both records have appearances in ${sharedYears.join(", ")} · ${
          sameContract ? "at least one shared contract" : "different contracts"
        }`;
    } else {
      const nearest = Math.min(
        ...left.living.flatMap((leftYear) =>
          right.living.map((rightYear) => Math.abs(leftYear - rightYear))
        ),
      );
      relationship = `Nearest dated appearances: ${nearest} year${nearest === 1 ? "" : "s"} apart`;
    }
  }

  return (
    <figure className="pl-career">
      <div className={groupSpan > 60 ? "pl-career-summary is-warning" : "pl-career-summary"}>
        <strong>{relationship}</strong>
        {groupSpan <= 60 ? (
          <span>Shown within a consistent 60-year reference window</span>
        ) : null}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Dated contract appearances for ${rows.length} records, ${first} to ${last}; combined span ${groupSpan} years. Filled circles are living appearances, hollow diamonds are posthumous appearances, and dashed lines show only the observed first-to-last extent.`}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              className="pl-career-tick"
              x1={x(tick)}
              y1="2"
              x2={x(tick)}
              y2={H - 20}
            />
            <text className="pl-career-axis-year" x={x(tick)} y={H - 4} textAnchor="middle">
              {tick}
            </text>
          </g>
        ))}
        <line className="pl-career-axis" x1={labelW} y1={H - 20} x2={W - 18} y2={H - 20} />
        {rows.map((row, index) => {
          const y = 10 + index * rowH;
          const start = row.livingFirst === null ? null : x(row.livingFirst);
          const end = row.livingLast === null ? null : x(row.livingLast);
          const oneYear = row.livingFirst !== null && row.livingFirst === row.livingLast;
          const rangeLabel = row.livingFirst === null
            ? (row.posthumous.length ? "No dated living appearance" : "Undated")
            : oneYear
              ? String(row.livingFirst)
              : `${row.livingFirst}–${row.livingLast}`;
          const label = row.undated ? `${rangeLabel} · +${row.undated} undated` : rangeLabel;
          return (
            <g key={row.person.person_id}>
              <title>
                {row.person.display_name}: {
                  row.livingFirst === null
                    ? "no dated living appearance"
                    : row.livingFirst === row.livingLast
                      ? String(row.livingFirst)
                      : `${row.livingFirst}–${row.livingLast}`
                }
              </title>
              <text className="pl-career-name" x="0" y={y + 12}>
                #{row.person.person_id} · {row.person.display_name.slice(0, 22)}
              </text>
              <text className="pl-career-range" x="0" y={y + 27}>{label}</text>
              <line className="pl-career-guide" x1={labelW} y1={y + 17} x2={W - 18} y2={y + 17} />
              {start !== null && end !== null ? (
                <>
                  {!oneYear ? (
                    <line
                      className="pl-career-extent"
                      x1={start}
                      y1={y + 17}
                      x2={end}
                      y2={y + 17}
                    />
                  ) : null}
                  {row.living.map((year) => {
                    const appearances = row.livingByYear.get(year) ?? [];
                    return (
                      <g
                        className="pl-career-event"
                        key={`${row.person.person_id}-${year}`}
                        tabIndex={0}
                        role="img"
                        aria-label={`${row.person.display_name}: ${appearances.length} dated contract appearance${appearances.length === 1 ? "" : "s"} in ${year}`}
                      >
                        <title>
                          {appearances.map((appearance) => (
                            `${appearance.registration_date ?? year} · contract ${appearance.contract_id}`
                            + `${appearance.firm_name ? ` · ${appearance.firm_name}` : ""}`
                            + `${appearance.roles ? ` · ${appearance.roles}` : ""}`
                          )).join("\n")}
                        </title>
                        <circle
                          className="pl-career-point"
                          cx={x(year)}
                          cy={y + 17}
                          r={appearances.length > 1 ? 7 : 5}
                        />
                        {appearances.length > 1 ? (
                          <text className="pl-career-count" x={x(year)} y={y + 19.5} textAnchor="middle">
                            {appearances.length}
                          </text>
                        ) : null}
                      </g>
                    );
                  })}
                </>
              ) : (
                <circle className="pl-career-undated" cx={labelW + 8} cy={y + 17} r="4" />
              )}
              {row.posthumous.map((year) => {
                const appearances = row.posthumousByYear.get(year) ?? [];
                const pointX = x(year);
                const pointY = y + 17;
                return (
                  <g
                    className="pl-career-event"
                    key={`${row.person.person_id}-h-${year}`}
                    tabIndex={0}
                    role="img"
                    aria-label={`${row.person.display_name}: ${appearances.length} posthumous appearance${appearances.length === 1 ? "" : "s"} in ${year}`}
                  >
                    <title>
                      Posthumous (heirs of) · {year} · contract{
                        appearances.length === 1 ? ` ${appearances[0].contract_id}` : `s ${appearances.map((item) => item.contract_id).join(", ")}`
                      }
                    </title>
                    <polygon
                      className="pl-career-posthumous"
                      points={`${pointX},${pointY - 6} ${pointX + 6},${pointY} ${pointX},${pointY + 6} ${pointX - 6},${pointY}`}
                    />
                    {appearances.length > 1 ? (
                      <text className="pl-career-count is-posthumous" x={pointX} y={y + 19.5} textAnchor="middle">
                        {appearances.length}
                      </text>
                    ) : null}
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <figcaption>
        <span><i className="pl-career-legend-event" /> dated contract</span>
        <span><i className="pl-career-legend-extent" /> first-to-last observed extent, not continuity</span>
        {rows.some((row) => row.posthumous.length) ? (
          <span><i className="pl-career-legend-posthumous" /> posthumous (heirs of)</span>
        ) : null}
      </figcaption>
    </figure>
  );
}
