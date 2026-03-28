function getBadgeClass(state) {
  switch (state) {
    case 'HEALED':   return 'badge-healed';
    case 'HEALTHY':  return 'badge-healthy';
    case 'WATCHING': return 'badge-watching';
    case 'ANOMALY':  return 'badge-anomaly';
    default:         return 'badge-watching';
  }
}

export default function ServiceStatus({ services, onSelectService, selectedService }) {
  return (
    <div className="card">
      <div className="card-label">SERVICE STATUS</div>
      <div className="service-list">
        {Object.entries(services).map(([name, svcState]) => {
          let status = 'HEALTHY';
          if (svcState.is_anomaly && svcState.confidence >= 80) status = 'ANOMALY';
          else if (svcState.is_anomaly) status = 'WATCHING';

          // If backend returned a recovery status for this service, use it
          if (svcState._status) status = svcState._status;

          return (
            <div
              key={name}
              className="service-row"
              style={selectedService === name ? { borderColor: 'var(--purple-bg)', background: 'var(--purple-bg)' } : {}}
              onClick={() => onSelectService(name)}
              title="Click to view vote buffer & latency"
            >
              <span className="service-name">{name}</span>
              <span className={`badge ${getBadgeClass(status)}`}>{status}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
