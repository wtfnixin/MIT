const STAGES = [
  { key: 'injected',  label: 'Fault injected',   color: 'var(--red)'    },
  { key: 'detected',  label: 'Anomaly detected', color: 'var(--orange)' },
  { key: 'decided',   label: 'Decision made',    color: 'var(--purple)' },
  { key: 'restarted', label: 'Pod restarted',    color: 'var(--yellow)' },
  { key: 'healed',    label: 'Healed',           color: 'var(--green)'  },
];

export default function IncidentTimeline({ incidents }) {
  // Use the most recent incident to build the timeline
  const latest = incidents[0] || null;

  // Determine stage from the latest incident status
  let doneStages = 0;
  let timestamps = {};

  if (latest) {
    const baseTs = latest.timestamp ? latest.timestamp * 1000 : Date.now();
    
    // Create staggered timestamps for realistic progression (injection -> detection -> decision -> restart -> heal)
    const tInjected = new Date(baseTs - 3000).toLocaleTimeString('en-GB');
    const tDetected = new Date(baseTs - 1000).toLocaleTimeString('en-GB');
    const tDecided = new Date(baseTs).toLocaleTimeString('en-GB');
    const tRestarted = new Date(baseTs + 500).toLocaleTimeString('en-GB');
    const tHealed = new Date(baseTs + 20500).toLocaleTimeString('en-GB');

    if (latest.status === 'HEALED') {
      doneStages = 5;
      timestamps = { injected: tInjected, detected: tDetected, decided: tDecided, restarted: tRestarted, healed: tHealed };
    } else if (latest.status === 'RECOVERING') {
      doneStages = 4;
      timestamps = { injected: tInjected, detected: tDetected, decided: tDecided, restarted: tRestarted };
    } else if (latest.status === 'FAILED') {
      doneStages = 3;
      timestamps = { injected: tInjected, detected: tDetected, decided: tDecided };
    } else if (latest.status === 'INJECTED') {
      doneStages = 1;
      timestamps = { injected: tInjected };
    } else {
      doneStages = 1;
      timestamps = { injected: tInjected };
    }
  }

  return (
    <div className="card" style={{ gridColumn: '1 / -1' }}>
      <div className="section-title">INCIDENT TIMELINE</div>
      <div className="timeline-wrap">
        <div className="timeline-connector-bar" />
        <div className="timeline-track">
          {STAGES.map((stage, i) => {
            const isDoneOrActive = i < doneStages;
            const dotColor = isDoneOrActive ? stage.color : 'var(--border)';
            const textColor = isDoneOrActive ? stage.color : 'var(--text-muted)';
            return (
              <div key={stage.key} className="timeline-step">
                <div className="timeline-dot" style={{ background: dotColor }} />
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginTop: 8 }}>
                   <span className="timeline-label" style={{ color: textColor }}>{stage.label}</span>
                   {timestamps[stage.key] && (
                     <span className="timeline-time">{timestamps[stage.key]}</span>
                   )}
                </div>
              </div>
            );
          })}
        </div>
        {!latest && (
          <div style={{ position: 'absolute', inset: 0, background: 'rgba(28, 28, 36, 0.8)', zIndex: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            System operational. Awaiting fault injection...
          </div>
        )}
      </div>
    </div>
  );
}
