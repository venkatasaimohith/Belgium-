window.BELGIUM = window.BELGIUM || {};

BELGIUM.THEME_STORAGE_KEY = "belgium_theme";
BELGIUM._themeListeners = BELGIUM._themeListeners || new Set();

BELGIUM.getTheme = function getTheme() {
  try {
    const stored = localStorage.getItem(BELGIUM.THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch (_) {}
  return "dark";
};

BELGIUM.notifyThemeChange = function notifyThemeChange() {
  BELGIUM._themeListeners.forEach((listener) => {
    try {
      listener(BELGIUM.getTheme());
    } catch (_) {}
  });
};

BELGIUM.applyTheme = function applyTheme(theme) {
  const resolved = theme === "light" ? "light" : "dark";
  const root = document.documentElement;
  const body = document.body;

  root.classList.remove("belgium-theme-dark", "belgium-theme-light", "dark", "light");
  if (resolved === "light") {
    root.classList.add("belgium-theme-light", "light");
  } else {
    root.classList.add("belgium-theme-dark", "dark");
  }

  if (body) {
    body.classList.remove(
      "bg-slate-950",
      "text-slate-100",
      "bg-slate-50",
      "bg-sky-50",
      "text-slate-900"
    );
    if (resolved === "light") {
      body.classList.add("text-slate-900");
    } else {
      body.classList.add("text-slate-100");
    }
  }

  try {
    localStorage.setItem(BELGIUM.THEME_STORAGE_KEY, resolved);
  } catch (_) {}

  BELGIUM.notifyThemeChange();
  return resolved;
};

BELGIUM.setTheme = function setTheme(theme) {
  return BELGIUM.applyTheme(theme);
};

BELGIUM.toggleTheme = function toggleTheme() {
  const next = BELGIUM.getTheme() === "dark" ? "light" : "dark";
  return BELGIUM.applyTheme(next);
};

BELGIUM.initTheme = function initTheme() {
  return BELGIUM.applyTheme(BELGIUM.getTheme());
};

BELGIUM.useTheme = function useTheme() {
  const [, setRevision] = React.useState(0);

  React.useEffect(() => {
    function onThemeChange() {
      setRevision((value) => value + 1);
    }
    BELGIUM._themeListeners.add(onThemeChange);
    return () => BELGIUM._themeListeners.delete(onThemeChange);
  }, []);

  const theme = BELGIUM.getTheme();

  function setTheme(nextTheme) {
    return BELGIUM.applyTheme(nextTheme);
  }

  function toggleThemeHandler() {
    return BELGIUM.toggleTheme();
  }

  return { theme, setTheme, toggleTheme: toggleThemeHandler, isDark: theme === "dark" };
};

BELGIUM.themeClasses = function themeClasses(lightClass, darkClass) {
  return BELGIUM.getTheme() === "light" ? lightClass : darkClass;
};

BELGIUM.useThemeTokens = function useThemeTokens() {
  const { theme, isDark } = BELGIUM.useTheme();
  const isLight = !isDark;

  return {
    theme,
    isLight,
    isDark,
    appBackground: "bg-transparent",
    pageBackground: "bg-transparent",
    headerEyebrow: isLight ? "text-cyan-700" : "text-slate-400",
    pageTitle: isLight ? "text-slate-950" : "text-slate-100",
    pageSubtitle: isLight ? "text-slate-600" : "text-slate-400",
    pageBg: "bg-transparent",
    surface: isLight
      ? "rounded-2xl border border-white/55 bg-white/58 backdrop-blur-xl shadow-[0_10px_40px_rgba(15,23,42,0.08)]"
      : "rounded-2xl border border-white/12 bg-slate-900/55 backdrop-blur-xl shadow-[0_14px_48px_rgba(0,0,0,0.34)]",
    surfaceSoft: isLight
      ? "border border-white/45 bg-white/42 backdrop-blur-lg"
      : "border border-white/10 bg-slate-950/45 backdrop-blur-lg",
    surfaceSelected: isLight
      ? "border-cyan-400 bg-cyan-50 shadow-sm"
      : "border-cyan-400 bg-cyan-500/10",
    border: isLight ? "border-slate-200" : "border-slate-800",
    textPrimary: isLight ? "text-slate-950" : "text-slate-100",
    textSecondary: isLight ? "text-slate-600" : "text-slate-400",
    textMuted: isLight ? "text-slate-500" : "text-slate-500",
    headingText: isLight ? "text-slate-950" : "text-slate-100",
    mutedText: isLight ? "text-slate-600" : "text-slate-400",
    subtleText: isLight ? "text-slate-500" : "text-slate-500",
    buttonPrimary: "bg-cyan-500 text-slate-950 hover:bg-cyan-400",
    buttonSecondary: isLight
      ? "border border-slate-200 bg-slate-100 text-slate-800 hover:bg-slate-200"
      : "bg-slate-800 text-slate-100 hover:bg-slate-700",
    badgeCompleted: isLight ? "bg-emerald-100 text-emerald-700" : "bg-emerald-500/20 text-emerald-200",
    badgeUploaded: isLight ? "bg-amber-100 text-amber-700" : "bg-amber-500/20 text-amber-100",
    badgeFailed: isLight ? "bg-red-100 text-red-700" : "bg-rose-500/20 text-rose-200",
    stageCard: isLight
      ? "rounded-xl border border-slate-200 bg-white p-3"
      : "rounded-xl border border-slate-800 bg-slate-950/60 p-3",
    eventPanel: isLight
      ? "rounded-xl border border-slate-200 bg-white p-3 text-sm"
      : "rounded-xl border border-slate-800 bg-slate-950 p-3 text-sm",
    panel: isLight
      ? "border border-slate-200 bg-white"
      : "border border-slate-800 bg-slate-950/60",
    panelSoft: isLight
      ? "border border-slate-200 bg-slate-50"
      : "border border-slate-800 bg-slate-950/50",
    inset: isLight
      ? "border border-slate-200 bg-white"
      : "border border-slate-800 bg-slate-950",
    progressTrack: isLight ? "bg-slate-200" : "bg-slate-800",
    divider: isLight ? "border-slate-200" : "border-slate-800",
    ghostButton: isLight
      ? "border border-slate-200 bg-slate-100 text-slate-800 hover:bg-slate-200"
      : "bg-slate-800 text-slate-100 hover:bg-slate-700",
    errorBox: isLight
      ? "rounded-lg bg-rose-100 p-3 text-rose-800"
      : "rounded-lg bg-rose-500/20 p-3 text-rose-200",
    jobCard: (selected) =>
      selected
        ? isLight
          ? "border-cyan-400 bg-cyan-50 shadow-sm"
          : "border-cyan-400 bg-cyan-500/10"
        : isLight
          ? "border-slate-200 bg-white hover:border-cyan-200 hover:bg-sky-50"
          : "border-slate-800 bg-slate-950/50 hover:bg-slate-800/50",
    emptyState: isLight
      ? "rounded-xl border border-dashed border-slate-300 bg-white"
      : "rounded-xl border border-dashed border-slate-700",
    accentText: isLight ? "text-cyan-700" : "text-cyan-300",
    warningText: isLight ? "text-amber-700" : "text-amber-400",
    disabledChip: isLight ? "bg-slate-100 text-slate-500" : "bg-slate-800/60 text-slate-500",
  };
};
