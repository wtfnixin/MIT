export default function StatCard({ label, value, sub, valueStyle }) {
  return (
    <div className="card">
      <div className="card-label">{label}</div>
      <div className="card-value" style={valueStyle}>{value}</div>
      {sub && <div className="card-sub">{sub}</div>}
    </div>
  );
}
