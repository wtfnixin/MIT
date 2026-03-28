import { useEffect, useState } from 'react';

export default function Header({ connected, warmupDone }) {
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const time = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  return (
    <header className="header">
      <div className="header-logo">KubeResilience</div>
      <div className="header-spacer" />
      {!warmupDone && (
        <span className="badge badge-warming">Warming up ...</span>
      )}
      <div className="connection-pill">
        <span className={`dot ${connected ? 'dot-green' : 'dot-red'}`} />
        <span style={{ color: connected ? 'var(--text)' : 'var(--red)', fontWeight: 600 }}>
          {connected ? 'Backend connected' : 'Backend offline'}
        </span>
      </div>
      <span style={{ color: 'var(--text-muted)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>Polling every 2s</span>
      <span className="header-clock" style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-dim)', marginLeft: 16 }}>{time}</span>
    </header>
  );
}
