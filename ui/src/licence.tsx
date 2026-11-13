import { useState } from "react";

// a licence: the clean ones by name, or Other licence… with the owner's own words (bought, written permission, a
// custom one); "" is the `none` choice. Batch import's Set licence and Setup's clip pack clearance use it.
export const LICENCES: [string, string][] = [
  ["own", "My own work"],
  ["CC0-1.0", "CC0"],
  ["MIT", "MIT"],
  ["Apache-2.0", "Apache-2.0"],
];

// a licence as the app shows it: a known one by its name, the owner's own words as they wrote them
export const licenceName = (v: string) => LICENCES.find(([k]) => k === v)?.[1] ?? v;

export function LicencePicker(props: { label: string; none: string; value: string; onChange: (v: string) => void }) {
  const [other, setOther] = useState(props.value !== "" && !LICENCES.some(([v]) => v === props.value));
  return (
    <>
      <select
        aria-label={props.label}
        value={other ? "*" : props.value}
        onChange={(e) => (setOther(e.target.value === "*"), props.onChange(e.target.value === "*" ? "" : e.target.value))}
      >
        <option value="">{props.none}</option>
        {LICENCES.map(([v, t]) => (
          <option key={v} value={v}>
            {t}
          </option>
        ))}
        <option value="*">Other licence…</option>
      </select>
      {other && (
        <input
          aria-label={`${props.label}: other licence`}
          placeholder="Its name and terms, e.g. Bought from …, commercial use"
          value={props.value}
          autoFocus
          onChange={(e) => props.onChange(e.target.value)}
        />
      )}
    </>
  );
}
