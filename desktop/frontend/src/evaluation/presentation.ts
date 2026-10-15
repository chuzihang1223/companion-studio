import type { EvaluationRun } from './types';
export const terminal = (status: string) =>
  ['COMPLETED', 'FAILED', 'CANCELLED', 'INTERRUPTED'].includes(status);
export const rate = (value?: number | null) =>
  value == null ? 'N/A' : `${(value * 100).toFixed(1)}%`;
export const number = (value?: number | null) => (value == null ? 'N/A' : value.toFixed(1));
export const modelLabel = (run?: EvaluationRun | null) => run?.snapshot.model.model_id || 'N/A';
// Dashboard has no per-event Workbench state: at most one local refresh/500 ms.
// A bounded series also makes synthetic 1000-record histories inexpensive to plot.
export function sampleSeries(values: number[], maximum = 80): number[] {
  const finite = values.filter(Number.isFinite);
  if (finite.length <= maximum) return finite;
  const stride = finite.length / maximum;
  return Array.from({ length: maximum }, (_, i) => finite[Math.floor(i * stride)]);
}
