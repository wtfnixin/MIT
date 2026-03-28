import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell, ResponsiveContainer,
} from 'recharts';

const NORMAL_COLOR   = 'var(--green)';
const ANOMALY_COLOR  = 'var(--red)';
const RECOVER_COLOR  = 'var(--yellow)';

function getColor(entry) {
  if (entry.status === 'anomaly')   return ANOMALY_COLOR;
  if (entry.status === 'recovered') return RECOVER_COLOR;
  return NORMAL_COLOR;
}

const CustomTooltip = ({ active, payload }) => {
  if (active && payload && payload.length) {
    const d = payload[0].payload;
    return (
      <div style={{
        background: 'var(--panel)', border: '1px solid var(--border)',
        padding: '12px 16px', borderRadius: 'var(--radius)', 
        fontSize: 12, fontFamily: 'var(--font-mono)'
      }}>
        <div style={{ color: 'var(--text-muted)', marginBottom: 8, fontSize: 10 }}>Window {d.window}</div>
        <div style={{ color: getColor(d), fontSize: 16, fontWeight: 700, fontFamily: 'var(--font-sans)' }}>{d.latency.toFixed(1)}ms</div>
        <div style={{ color: 'var(--text-dim)', marginTop: 8, textTransform: 'uppercase', fontSize: 10 }}>{d.status}</div>
      </div>
    );
  }
  return null;
};

export default function LatencyChart({ history, service }) {
  const data = history.map((h, i) => ({ window: i + 1, ...h }));

  return (
    <div className="card">
      <div className="section-title">P95 LATENCY (LAST 10 WINDOWS)</div>
      <div className="chart-wrap" style={{ height: 220, width: '100%' }}>
        <ResponsiveContainer width="100%" height="100%" minWidth={100} minHeight={200}>
          <BarChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }} barCategoryGap="15%">
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="window" tick={{ fontSize: 10, fill: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fontSize: 10, fill: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }} axisLine={false} tickLine={false} unit="ms" />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(255,255,255,0.02)' }} />
            <Bar dataKey="latency" radius={[2, 2, 0, 0]}>
              {data.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={getColor(entry)} opacity={1.0} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="chart-legend" style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16, fontSize: 11, fontFamily: 'var(--font-mono)', letterSpacing: '0.05em' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: NORMAL_COLOR }}><span>normal</span></div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: ANOMALY_COLOR }}><span>anomaly</span></div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: RECOVER_COLOR }}><span>recovered</span></div>
      </div>
    </div>
  );
}
