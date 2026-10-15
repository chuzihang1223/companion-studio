import { memo } from 'react';
import { sampleSeries } from './presentation';

export default memo(function EvaluationTrend({ values }: { values: number[] }) {
  const points = sampleSeries(values, 80);
  const max = Math.max(1, ...points);
  if (!points.length) return <p className="evaluation-note">运行完成后显示历史平均耗时趋势。</p>;
  return (
    <figure className="evaluation-trend">
      <figcaption>历史平均耗时（各批条件可能不同；不能据此排名）</figcaption>
      <svg viewBox="0 0 400 60" role="img" aria-label="历史评测耗时趋势">
        <polyline
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          points={points
            .map((n, i) => `${(i * 400) / Math.max(1, points.length - 1)},${58 - (n / max) * 54}`)
            .join(' ')}
        />
      </svg>
    </figure>
  );
});
