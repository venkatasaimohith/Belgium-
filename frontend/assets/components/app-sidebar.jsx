window.BELGIUM = window.BELGIUM || {};

BELGIUM.NAV_ITEMS = [
  { id: "projects", label: "Projects", icon: "grid" },
  { id: "chat", label: "Ollama Chat", icon: "chat" },
  { id: "api-keys", label: "API Keys", icon: "key" },
];

BELGIUM.sidebarUserInitials = function sidebarUserInitials(email) {
  if (!email) return "BE";
  const local = String(email).split("@")[0] || "";
  const parts = local.split(/[._-]/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return local.slice(0, 2).toUpperCase() || "BE";
};

BELGIUM.SidebarIconButton = function SidebarIconButton({ label, active, onClick, isLight, children }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      className={`flex h-11 w-11 items-center justify-center rounded-xl transition ${
        active
          ? isLight
            ? "bg-cyan-100 text-cyan-700 shadow-sm"
            : "bg-cyan-500/20 text-cyan-300"
          : isLight
            ? "text-slate-600 hover:bg-slate-100"
            : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
      }`}
    >
      {children}
    </button>
  );
};

BELGIUM.SidebarNavIcon = function SidebarNavIcon({ name }) {
  if (name === "chat") {
    return (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" />
      </svg>
    );
  }
  if (name === "key") {
    return (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <circle cx="8" cy="15" r="4" />
        <path d="m12 11 8-8M16 7l2 2" />
      </svg>
    );
  }
  return (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  );
};

BELGIUM.AppSidebar = function AppSidebar({ activePage, onNavigate, user, onLogout, theme, onToggleTheme }) {
  const isLight = theme === "light";
  const initials = BELGIUM.sidebarUserInitials(user?.email);

  return (
    <aside
      className="app-sidebar flex h-screen w-[72px] flex-shrink-0 flex-col items-center py-3"
      aria-label="Application navigation"
    >
      <button
        type="button"
        title="Belgium Studio"
        aria-label="Belgium Studio"
        onClick={() => onNavigate("projects")}
        className={`mb-4 flex h-11 w-11 items-center justify-center rounded-2xl text-sm font-black text-white shadow-sm ${
          isLight ? "bg-cyan-600" : "bg-cyan-500"
        }`}
      >
        B
      </button>

      <nav className="flex flex-col items-center gap-2">
        {BELGIUM.NAV_ITEMS.map((item) => (
          <BELGIUM.SidebarIconButton
            key={item.id}
            label={item.label}
            active={activePage === item.id}
            onClick={() => onNavigate(item.id)}
            isLight={isLight}
          >
            <BELGIUM.SidebarNavIcon name={item.icon} />
          </BELGIUM.SidebarIconButton>
        ))}
      </nav>

      <div className="mt-auto flex w-full flex-col items-center gap-2 px-2 pb-2">
        <BELGIUM.SidebarIconButton
          label={isLight ? "Switch to dark mode" : "Switch to light mode"}
          active={false}
          onClick={onToggleTheme}
          isLight={isLight}
        >
          {isLight ? (
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
            </svg>
          ) : (
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
            </svg>
          )}
        </BELGIUM.SidebarIconButton>

        <div
          title={user?.email || "Signed in"}
          className={`flex h-11 w-11 items-center justify-center rounded-full text-xs font-bold text-white ${
            isLight ? "bg-cyan-600" : "bg-cyan-500"
          }`}
        >
          {initials}
        </div>

        <BELGIUM.SidebarIconButton
          label="Sign out"
          active={false}
          onClick={onLogout}
          isLight={isLight}
        >
          <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <path d="M16 17l5-5-5-5" />
            <path d="M21 12H9" />
          </svg>
        </BELGIUM.SidebarIconButton>
      </div>
    </aside>
  );
};
