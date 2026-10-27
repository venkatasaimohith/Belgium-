window.BELGIUM = window.BELGIUM || {};

BELGIUM.Button = function Button({ children, className = "", variant = "primary", ...props }) {
  const tokens = BELGIUM.useThemeTokens();
  const styles =
    variant === "secondary"
      ? tokens.buttonSecondary
      : tokens.buttonPrimary;

  return (
    <button
      className={`rounded-lg px-4 py-2 font-medium transition disabled:opacity-50 ${styles} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
};

BELGIUM.Card = function Card({ children, className = "" }) {
  const tokens = BELGIUM.useThemeTokens();
  return <div className={`${tokens.surface} p-5 ${className}`}>{children}</div>;
};

BELGIUM.Badge = function Badge({ children, tone = "default" }) {
  const tokens = BELGIUM.useThemeTokens();
  const { isLight } = tokens;

  const tones = isLight
    ? {
        default: "bg-slate-100 text-slate-700",
        green: tokens.badgeCompleted,
        red: tokens.badgeFailed,
        yellow: tokens.badgeUploaded,
        blue: "bg-cyan-100 text-cyan-800",
        info: "bg-sky-100 text-sky-800",
        warning: "bg-amber-100 text-amber-700",
      }
    : {
        default: "bg-slate-700 text-slate-100",
        green: "bg-emerald-500/20 text-emerald-200",
        red: "bg-rose-500/20 text-rose-200",
        yellow: "bg-amber-500/20 text-amber-100",
        blue: "bg-cyan-500/20 text-cyan-100",
        info: "bg-sky-500/20 text-sky-200",
        warning: "bg-amber-500/20 text-amber-100",
      };

  return (
    <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${tones[tone] || tones.default}`}>
      {children}
    </span>
  );
};

BELGIUM.Input = function Input({ className = "", ...props }) {
  const { isLight } = BELGIUM.useTheme();
  const base = isLight
    ? "rounded-lg border border-slate-300/80 bg-white/90 px-3 py-2 text-slate-900 backdrop-blur-md placeholder:text-slate-500"
    : "rounded-lg border border-white/12 bg-slate-950/45 px-3 py-2 text-slate-100 backdrop-blur-md placeholder:text-slate-500";
  return <input className={`app-input ${base} ${className}`} {...props} />;
};

BELGIUM.Tabs = function Tabs({
  value,
  onChange,
  items,
  className = "",
  buttonClassName = "",
  activeClassName = "is-active",
}) {
  const { isLight } = BELGIUM.useTheme();
  const useLoginStyles = className.includes("login-tabs");
  return (
    <div
      className={
        useLoginStyles
          ? `flex gap-1 rounded-lg p-1 ${className}`
          : `flex gap-1 rounded-lg p-1 backdrop-blur-md ${
              isLight ? "border border-white/45 bg-white/35" : "border border-white/10 bg-slate-950/35"
            } ${className}`
      }
    >
      {(items || []).map((item) => {
        const active = value === item.id;
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onChange(item.id)}
            className={
              useLoginStyles
                ? `${buttonClassName}${active ? ` ${activeClassName}` : ""}`
                : `flex-1 rounded-md px-3 py-2 text-sm font-medium transition ${
                    active
                      ? isLight
                        ? "border border-white/60 bg-white/75 text-slate-900 shadow backdrop-blur-md"
                        : "border border-white/12 bg-slate-800/70 text-white shadow backdrop-blur-md"
                      : isLight
                        ? "text-slate-800 hover:text-slate-950"
                        : "text-slate-400 hover:text-white"
                  } ${buttonClassName}${active && !useLoginStyles ? ` ${activeClassName}` : ""}`
            }
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
};

BELGIUM.SummaryCard = function SummaryCard({ label, value, tone = "default" }) {
  const tokens = BELGIUM.useThemeTokens();
  return (
    <div className={`rounded-xl border p-4 ${tokens.border} ${tokens.isLight ? "bg-white" : "bg-slate-950/60"}`}>
      <div className={`text-xs uppercase tracking-wide ${tokens.textMuted}`}>{label}</div>
      <div className={`mt-2 text-2xl font-bold ${tokens.textPrimary}`}>{value}</div>
      {tone !== "default" && (
        <div className="mt-2">
          <BELGIUM.Badge tone={tone}>{tone}</BELGIUM.Badge>
        </div>
      )}
    </div>
  );
};

BELGIUM.statusTone = function statusTone(status) {
  if (status === "COMPLETED") return "green";
  if (status === "FAILED") return "red";
  if (status === "PROCESSING" || status === "QUEUED" || status === "STOPPING") return "blue";
  if (status === "UPLOADED") return "yellow";
  return "default";
};

BELGIUM.StatusBadge = function StatusBadge({ status, pulse = false }) {
  const normalized = String(status || "default").toLowerCase();
  const className =
    normalized === "completed"
      ? "status-completed"
      : normalized === "processing" || normalized === "queued" || normalized === "stopping"
        ? "status-processing"
        : normalized === "uploaded"
          ? "status-uploaded"
          : normalized === "failed"
            ? "status-failed"
            : "status-default";

  return (
    <span className={`status-badge ${className}`}>
      {pulse && <span className="status-pulse" />}
      {status}
    </span>
  );
};

BELGIUM.ProgressBar = function ProgressBar({ progress = 0, isActive = false, label, compact = false }) {
  const value = Math.max(0, Math.min(100, Number(progress) || 0));
  return (
    <div>
      {!compact && label && (
        <div className="mb-1 text-xs" style={{ color: "var(--app-text-soft)" }}>
          {label}
        </div>
      )}
      <div className={`app-progress-track ${compact ? "h-1.5" : ""}`}>
        <div
          className={`app-progress-fill ${isActive ? "is-active" : ""}`}
          style={{ width: `${value}%` }}
        />
      </div>
      {!compact && (
        <div className="progress-percent-label mt-1 text-right text-xs tabular-nums">{value}%</div>
      )}
    </div>
  );
};

BELGIUM.DashboardStatCard = function DashboardStatCard({ label, value, hint }) {
  return (
    <div className="app-card app-stat rounded-xl">
      <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--app-text-soft)" }}>
        {label}
      </div>
      <div className="mt-1 text-2xl font-bold">{value}</div>
      <div className="mt-0.5 text-[10px]" style={{ color: "var(--app-text-soft)" }}>
        {hint}
      </div>
    </div>
  );
};

BELGIUM.Breadcrumbs = function Breadcrumbs({ items }) {
  const list = Array.isArray(items) ? items : [];
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol className="breadcrumbs-list">
        {list.map((item, index) => {
          const isLast = index === list.length - 1;
          return (
            <li key={`${item.label}-${index}`} className="inline-flex items-center gap-1">
              {index > 0 && <span className="breadcrumbs-sep">/</span>}
              {item.onClick && !isLast ? (
                <button type="button" className="breadcrumbs-link" onClick={item.onClick}>
                  {item.label}
                </button>
              ) : (
                <span className={isLast ? "breadcrumbs-current truncate" : "breadcrumbs-link"}>
                  {item.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
};

BELGIUM.EmptyState = function EmptyState({ icon, title, description, action }) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-state-icon">{icon}</div>}
      {title && <h3 className="empty-state-title">{title}</h3>}
      {description && <p className="empty-state-desc">{description}</p>}
      {action}
    </div>
  );
};

BELGIUM.Spinner = function Spinner({ size = 16, className = "" }) {
  return (
    <span
      className={`app-spinner ${className}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    />
  );
};

BELGIUM.Modal = function Modal({ open, title, children, onClose, footer, size = "md" }) {
  React.useEffect(() => {
    if (!open) return undefined;
    function onKey(event) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const sizeClass =
    size === "lg" ? "max-w-4xl" : size === "sm" ? "max-w-md" : "max-w-2xl";

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className={`modal-panel ${sizeClass}`}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="belgium-modal-title"
      >
        <div className="modal-header">
          <h2 id="belgium-modal-title" className="modal-title">
            {title}
          </h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
};

BELGIUM.ErrorBoundary = class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    if (typeof console !== "undefined" && console.error) {
      console.error("BELGIUM ErrorBoundary caught:", error, info);
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="rounded-lg bg-amber-500/20 p-3 text-sm text-amber-200">
          {this.props.fallback || "Something went wrong rendering this section."}
        </div>
      );
    }
    return this.props.children;
  }
};
