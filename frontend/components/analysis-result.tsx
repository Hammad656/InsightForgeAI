"use client";

import { useState } from "react";

type Cell = string | number | null;
export type Answer = {
  query_id: string;
  status: string;
  answer: string;
  question?: string;
  result?: { columns: string[]; rows: Record<string, Cell>[] };
  visualization?: { type: string; x: string; y: string };
  evidence?: {
    metric_id: string; metric_version: number; unit: string; dataset_versions: string[];
    published_at: string; generated_at: string; filters: Record<string, Cell>;
    query_hash: string; row_count: number; timezone: string; warnings: string[];
  };
  usage?: { latency_ms: number; llm_tokens: number };
};

const surface = "glass-card";
const label = (value: string) => value.replaceAll("_", " ");

export function Result({ answer, referenceLabel = "Query ID" }: { answer: Answer; referenceLabel?: string }) {
  const [selected, setSelected] = useState<number>();
  const chart = answer.visualization;
  const rows = answer.result?.rows ?? [];
  const columns = answer.result?.columns ?? [];
  const max = chart ? Math.max(1, ...rows.map((row) => Math.abs(Number(row[chart.y])))) : 1;
  return <article className={surface + " grid gap-4"}>
    <div>
      <p className="text-sm font-medium capitalize text-teal-800">{label(answer.status)}</p>
      <h2 className="mt-1 text-lg font-semibold">{answer.question ?? "Analysis result"}</h2>
      <p className="mt-2 text-slate-700">{answer.answer}</p>
    </div>
    {chart && chart.type !== "table" && rows.length > 0 && <section aria-label="Results chart">
      <h3 className="mb-2 font-semibold capitalize">{label(chart.y)} by {label(chart.x)}</h3>
      <ul className="grid max-h-64 gap-1 overflow-auto">
        {rows.map((row, index) => <li key={index}>
          <button type="button" aria-pressed={selected === index}
            onClick={() => setSelected(selected === index ? undefined : index)}
            className="grid w-full grid-cols-[minmax(4rem,8rem)_1fr_auto] items-center gap-3 rounded px-2 py-1 text-left text-sm hover:bg-teal-50 aria-pressed:bg-teal-100">
            <span className="truncate" title={String(row[chart.x])}>{row[chart.x]}</span>
            <span className="h-2 rounded bg-slate-100" aria-hidden="true">
              <span className="block h-full rounded bg-gradient-to-r from-violet-500 to-cyan-400" style={{ width: Math.abs(Number(row[chart.y])) / max * 100 + "%" }} />
            </span>
            <span className="tabular-nums">{row[chart.y] ?? "Unavailable"}</span>
          </button>
        </li>)}
      </ul>
    </section>}
    {rows.length > 0 && <div className="max-h-80 overflow-auto">
      <table aria-label="Verified results" className="w-full text-left text-sm">
        <thead className="sticky top-0 bg-slate-100"><tr>
          {columns.map((column) => <th scope="col" className="p-2 capitalize" key={column}>{label(column)}</th>)}
        </tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index} className={selected === index ? "bg-teal-100" : "border-b border-slate-100"}>
          {columns.map((column) => <td className="break-all p-2 tabular-nums" key={column}>{row[column] ?? "Unavailable"}</td>)}
        </tr>)}</tbody>
      </table>
    </div>}
    <details className="rounded-lg border border-slate-200 p-4">
      <summary className="cursor-pointer font-semibold">Evidence and usage</summary>
      <dl className="mt-3 grid gap-3 break-words text-sm sm:grid-cols-2">
        <div><dt className="font-medium">{referenceLabel}</dt><dd>{answer.query_id}</dd></div>
        {answer.usage && <div><dt className="font-medium">Processing</dt><dd>{answer.usage.latency_ms} ms · {answer.usage.llm_tokens} tokens</dd></div>}
        {answer.evidence && <>
          <div><dt className="font-medium">Metric</dt><dd>{label(answer.evidence.metric_id)} · v{answer.evidence.metric_version} · {answer.evidence.unit}</dd></div>
          <div><dt className="font-medium">Source version</dt><dd>{answer.evidence.dataset_versions.join(", ")}</dd></div>
          <div><dt className="font-medium">Published</dt><dd>{answer.evidence.published_at}</dd></div>
          <div><dt className="font-medium">Executed</dt><dd>{answer.evidence.generated_at}</dd></div>
          <div><dt className="font-medium">Filters · {answer.evidence.timezone}</dt><dd>
            {Object.entries(answer.evidence.filters).filter(([, value]) => value !== null)
              .map(([key, value]) => <span className="mr-2 inline-block" key={key}>{label(key)}: {value}</span>)}
          </dd></div>
          <div><dt className="font-medium">Query hash</dt><dd>{answer.evidence.query_hash}</dd></div>
          <div className="sm:col-span-2"><dt className="font-medium">Source coverage</dt><dd>{answer.evidence.warnings.join(" ")}</dd></div>
        </>}
      </dl>
    </details>
  </article>;
}
