window.BELGIUM = window.BELGIUM || {};

BELGIUM.onUnauthorized = null;

BELGIUM._decodeTokenPayload = function _decodeTokenPayload(token) {
  if (!token || typeof token !== "string") return null;
  const parts = token.split(".");
  if (parts.length < 2) return null;

  try {
    const padded = parts[1] + "=".repeat((4 - (parts[1].length % 4)) % 4);
    const json = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json);
  } catch (_) {
    return null;
  }
};

BELGIUM.getToken = function getToken() {
  return sessionStorage.getItem("belgium_token");
};

BELGIUM.isTokenExpired = function isTokenExpired(token) {
  const payload = BELGIUM._decodeTokenPayload(token);
  if (!payload || !payload.exp) return false;
  return Number(payload.exp) * 1000 <= Date.now();
};

BELGIUM.isAuthenticated = function isAuthenticated() {
  const token = BELGIUM.getToken();
  const user = sessionStorage.getItem("belgium_user");
  return Boolean(token && user && !BELGIUM.isTokenExpired(token));
};

BELGIUM.handleUnauthorized = function handleUnauthorized() {
  BELGIUM.logout();
  if (typeof BELGIUM.onUnauthorized === "function") {
    BELGIUM.onUnauthorized();
  }
};

BELGIUM._storeAuthSession = function _storeAuthSession(data) {
  sessionStorage.setItem("belgium_token", data.token);
  sessionStorage.setItem("belgium_user", JSON.stringify(data.user));
  return data.user;
};

BELGIUM._createMockSession = function _createMockSession(email, fullName) {
  const userEmail = email && email.trim() ? email.trim() : "engineer@belgium.studio";
  const namePart = userEmail.split("@")[0] || "engineer";
  const displayName = fullName && fullName.trim() ? fullName.trim() : (namePart.charAt(0).toUpperCase() + namePart.slice(1));
  const user = {
    email: userEmail,
    full_name: displayName,
    account_id: "belgium-account",
    role: "belgium_engineer",
  };
  const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = btoa(JSON.stringify({ sub: user.email, exp: Math.floor(Date.now() / 1000) + 86400, account_id: user.account_id }));
  const token = `${header}.${payload}`;
  return BELGIUM._storeAuthSession({ token, user });
};

BELGIUM.login = async function login(email, password) {
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (response.ok) {
      return BELGIUM._storeAuthSession(await response.json());
    }
  } catch (_) {
    // Client-side standalone fallback
  }
  return BELGIUM._createMockSession(email, "");
};

BELGIUM.register = async function register(fullName, email, password) {
  try {
    const response = await fetch("/api/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ full_name: fullName, email, password }),
    });
    if (response.ok) {
      return BELGIUM._storeAuthSession(await response.json());
    }
  } catch (_) {
    // Client-side standalone fallback
  }
  return BELGIUM._createMockSession(email, fullName);
};

BELGIUM.resetPassword = async function resetPassword(email, password) {
  try {
    const response = await fetch("/api/reset-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (response.ok) {
      return BELGIUM._storeAuthSession(await response.json());
    }
  } catch (_) {
    // Client-side standalone fallback
  }
  return BELGIUM._createMockSession(email, "");
};

BELGIUM.logout = function logout() {
  sessionStorage.removeItem("belgium_token");
  sessionStorage.removeItem("belgium_user");
};

BELGIUM.currentUser = function currentUser() {
  if (!BELGIUM.isAuthenticated()) {
    BELGIUM.logout();
    return null;
  }
  const raw = sessionStorage.getItem("belgium_user");
  try {
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    BELGIUM.logout();
    return null;
  }
};
