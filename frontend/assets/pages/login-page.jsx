window.BELGIUM = window.BELGIUM || {};

BELGIUM.LoginPage = function LoginPage({ onLogin }) {
  const [activeTab, setActiveTab] = React.useState("signin");
  const [error, setError] = React.useState("");
  const [info, setInfo] = React.useState("");
  const { theme, toggleTheme } = BELGIUM.useTheme();
  const isLight = theme === "light";

  function messageClass(kind) {
    if (kind === "error") {
      return isLight
        ? "login-alert login-alert--error"
        : "login-alert login-alert--error login-alert--error-dark";
    }
    return isLight
      ? "login-alert login-alert--info"
      : "login-alert login-alert--info login-alert--info-dark";
  }

  async function submitSignIn(event) {
    event.preventDefault();
    setError("");
    setInfo("");
    const form = event.currentTarget;
    try {
      const user = await BELGIUM.login(form.email.value, form.password.value);
      onLogin(user);
    } catch (err) {
      setError(err.message || "Invalid login");
    }
  }

  async function submitCreate(event) {
    event.preventDefault();
    setError("");
    setInfo("");
    const form = event.currentTarget;
    const password = form.password.value;
    const confirmPassword = form.confirm_password.value;
    if (password && password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    try {
      const user = await BELGIUM.register(form.full_name.value, form.email.value, password);
      onLogin(user);
    } catch (err) {
      setError(err.message || "Could not create account");
    }
  }

  async function submitForgot(event) {
    event.preventDefault();
    setError("");
    setInfo("");
    const form = event.currentTarget;
    const password = form.password.value;
    const confirmPassword = form.confirm_password.value;
    if (password && password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    try {
      const user = await BELGIUM.resetPassword(form.email.value, password);
      onLogin(user);
    } catch (err) {
      setError(err.message || "Could not reset password");
    }
  }

  return (
    <main className="login-page relative flex min-h-screen items-center justify-center overflow-hidden p-6">
      <div
        className="login-page__background absolute inset-0 z-0 bg-cover bg-center bg-no-repeat transition-all duration-700 opacity-55 mix-blend-luminosity"
        style={{ backgroundImage: "url('./assets/images/login-bg.png')" }}
        aria-hidden="true"
      />
      <div className="absolute inset-0 z-0 bg-gradient-to-tr from-slate-950 via-slate-950/80 to-slate-900/60" />

      <div className="absolute right-4 top-4 z-20">
        <button
          type="button"
          onClick={toggleTheme}
          className="login-theme-toggle rounded-lg px-3.5 py-2 text-xs font-bold transition hover:scale-105"
        >
          {isLight ? "🌙 Dark mode" : "☀️ Light mode"}
        </button>
      </div>

      <BELGIUM.Card className="login-page__card relative z-10 w-full max-w-md p-6 shadow-2xl backdrop-blur-xl border border-white/10 rounded-2xl">
        <div className="login-page__header text-center mb-5">
          <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-xl bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 shadow-inner">
            <BELGIUM.Icon name="map" size={24} />
          </div>
          <h1 className="login-page__title text-2xl font-extrabold text-white tracking-tight">Belgium Studio</h1>
          <p className="login-page__subtitle text-xs text-slate-400 mt-1">
            Google Street View & Spatial Validation Platform
          </p>
        </div>

        <BELGIUM.Tabs
          className="login-tabs"
          buttonClassName="login-tabs__btn"
          activeClassName="is-active"
          value={activeTab}
          onChange={(tab) => {
            setActiveTab(tab);
            setError("");
            setInfo("");
          }}
          items={[
            { id: "signin", label: "Sign in" },
            { id: "create", label: "Create account" },
            { id: "forgot", label: "Forgot password" },
          ]}
        />

        {activeTab === "signin" && (
          <form onSubmit={submitSignIn} className="mt-6 space-y-4">
            <label className="grid gap-1">
              <span className="login-field-label">Email</span>
              <BELGIUM.Input name="email" type="email" defaultValue="engineer@belgium.studio" placeholder="you@company.com" autoComplete="email" required />
            </label>
            <label className="grid gap-1">
              <span className="login-field-label">Password</span>
              <BELGIUM.Input name="password" type="password" defaultValue="password" placeholder="Enter your password" autoComplete="current-password" required />
            </label>
            {error && <div className={messageClass("error")}>{error}</div>}
            <BELGIUM.Button className="w-full" type="submit">
              Sign in
            </BELGIUM.Button>
          </form>
        )}

        {activeTab === "create" && (
          <form onSubmit={submitCreate} className="mt-6 space-y-4">
            <label className="grid gap-1">
              <span className="login-field-label">Full name</span>
              <BELGIUM.Input name="full_name" placeholder="Belgium Engineer" required />
            </label>
            <label className="grid gap-1">
              <span className="login-field-label">Email</span>
              <BELGIUM.Input name="email" type="email" placeholder="you@company.com" required />
            </label>
            <label className="grid gap-1">
              <span className="login-field-label">Password</span>
              <BELGIUM.Input name="password" type="password" placeholder="Create a password" required />
            </label>
            <label className="grid gap-1">
              <span className="login-field-label">Confirm password</span>
              <BELGIUM.Input name="confirm_password" type="password" placeholder="Confirm your password" required />
            </label>
            {error && <div className={messageClass("error")}>{error}</div>}
            {info && <div className={messageClass("info")}>{info}</div>}
            <BELGIUM.Button className="w-full" type="submit">
              Create account & sign in
            </BELGIUM.Button>
          </form>
        )}

        {activeTab === "forgot" && (
          <form onSubmit={submitForgot} className="mt-6 space-y-4">
            <label className="grid gap-1">
              <span className="login-field-label">Email</span>
              <BELGIUM.Input name="email" type="email" placeholder="you@company.com" required />
            </label>
            <label className="grid gap-1">
              <span className="login-field-label">New password</span>
              <BELGIUM.Input name="password" type="password" placeholder="Create a new password" required />
            </label>
            <label className="grid gap-1">
              <span className="login-field-label">Confirm new password</span>
              <BELGIUM.Input name="confirm_password" type="password" placeholder="Confirm your new password" required />
            </label>
            {error && <div className={messageClass("error")}>{error}</div>}
            {info && <div className={messageClass("info")}>{info}</div>}
            <BELGIUM.Button className="w-full" type="submit">
              Reset password & sign in
            </BELGIUM.Button>
          </form>
        )}
      </BELGIUM.Card>
    </main>
  );
};
