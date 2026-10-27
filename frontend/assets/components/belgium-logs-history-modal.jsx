window.BELGIUM = window.BELGIUM || {};

function formatLogTimestamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

BELGIUM.BelgiumLogsHistoryModal = function BelgiumLogsHistoryModal({ open, onClose, events }) {
  const rows = React.useMemo(() => (events || []).slice().reverse(), [events]);

  return (
    <BELGIUM.Modal
      open={open}
      title="Logs History"
      onClose={onClose}
      size="lg"
      footer={
        <button type="button" className="app-button app-btn-secondary rounded-lg px-4 py-2" onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="logs-history-modal">
        <p className="logs-history-summary tabular-nums">{rows.length} log entries</p>
        <div className="logs-history-list">
          {rows.length ? (
            rows.map((event) => (
              <div key={event.id || `${event.timestamp}-${event.message}`} className="logs-history-row">
                <div className="logs-history-row-meta">
                  <span className="logs-history-time">{formatLogTimestamp(event.timestamp)}</span>
                  <span className="logs-history-agent">[{event.agent || "system"}]</span>
                  {event.level && <span className={`logs-history-level logs-history-level--${String(event.level).toLowerCase()}`}>{event.level}</span>}
                </div>
                <div className="logs-history-message">{event.message}</div>
              </div>
            ))
          ) : (
            <BELGIUM.EmptyState title="No logs yet" description="Pipeline events will appear here after processing starts." />
          )}
        </div>
      </div>
    </BELGIUM.Modal>
  );
};
