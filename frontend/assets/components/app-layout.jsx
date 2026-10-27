window.BELGIUM = window.BELGIUM || {};

BELGIUM.AppLayout = function AppLayout({ children, activePage, onNavigate, user, onLogout }) {
  const { theme, toggleTheme } = BELGIUM.useTheme();
  const tokens = BELGIUM.useThemeTokens();

  React.useEffect(() => {
    document.body.classList.add("belgium-app-shell");
    return () => document.body.classList.remove("belgium-app-shell");
  }, []);

  return (
    <div className={`flex h-screen w-full overflow-hidden ${tokens.appBackground} ${tokens.textPrimary}`}>
      <BELGIUM.AppSidebar
        activePage={activePage}
        onNavigate={onNavigate}
        user={user}
        onLogout={onLogout}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
      <main
        className={`app-main-shell min-h-0 min-w-0 flex-1 overflow-hidden p-4 sm:p-6 ${tokens.textPrimary}`}
      >
        {children}
      </main>
    </div>
  );
};
