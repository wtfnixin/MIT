export default function VoteBuffer({ votes, confidence, service }) {
  // votes is an array of 1/0 values, max window size = 5
  const windowSize = 5;
  const slots = Array.from({ length: windowSize }, (_, i) => votes[i] ?? null);
  const anomCount = votes.filter(v => v === 1).length;

  return (
    <div className="card">
      <div className="card-label">VOTE BUFFER</div>
      <div className="vote-slots" style={{ marginBottom: 24 }}>
        {slots.map((v, i) => (
          <div
            key={i}
            className={`vote-slot ${v === 1 ? 'vote-slot-anomaly' : 'vote-slot-empty'}`}
          >
            {v === 1 ? '!' : '-'}
          </div>
        ))}
        <span style={{ fontSize: 13, color: 'var(--text-dim)', marginLeft: 8, fontFamily: 'var(--font-sans)' }}>
          {anomCount} anomalous
        </span>
      </div>
      
      <div className="card-label">CONFIDENCE</div>
      <div className="conf-bar-wrap" style={{ marginTop: 0 }}>
        <div className="conf-bar-track" style={{ marginBottom: 12 }}>
          <div
            className="conf-bar-fill"
            style={{ width: `${Math.min(confidence, 100)}%` }}
          />
        </div>
        <div className="conf-bar-labels" style={{ marginTop: 0, marginBottom: 0 }}>
          <span>0%</span>
          <span style={{ color: 'var(--purple)', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{confidence.toFixed(0)}%</span>
          <span>100%</span>
        </div>
      </div>
    </div>
  );
}
