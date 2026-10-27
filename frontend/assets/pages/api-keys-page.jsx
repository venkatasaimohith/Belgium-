window.BELGIUM = window.BELGIUM || {};

function ApiKeyValidationBadge({ result }) {
  if (!result) return null;
  const className =
    result.status === "valid"
      ? "api-key-badge api-key-badge-valid"
      : result.status === "invalid" || result.status === "error"
        ? "api-key-badge api-key-badge-invalid"
        : result.status === "missing"
          ? "api-key-badge api-key-badge-missing"
          : "api-key-badge api-key-badge-unchecked";
  const labels = {
    valid: "Valid",
    invalid: "Not Valid",
    missing: "Missing",
    unchecked: "Not validated",
    error: "Not Valid",
  };
  return <span className={className}>{labels[result.status] || "Not validated"}</span>;
}

function ApiKeySetBadge({ isSet }) {
  return (
    <span className={`api-key-badge ${isSet ? "api-key-badge-set" : "api-key-badge-missing"}`}>
      {isSet ? "Set" : "Missing"}
    </span>
  );
}

BELGIUM.ApiKeysPage = function ApiKeysPage() {
  const [items, setItems] = React.useState([]);
  const [draft, setDraft] = React.useState({});
  const [envFile, setEnvFile] = React.useState("");
  const [backendAvailable, setBackendAvailable] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [validatingKey, setValidatingKey] = React.useState("");
  const [validatingAll, setValidatingAll] = React.useState(false);
  const [validationResults, setValidationResults] = React.useState({});
  const [error, setError] = React.useState("");
  const [notice, setNotice] = React.useState("");

  React.useEffect(() => {
    let active = true;
    setLoading(true);
    BELGIUM.settingsApi
      .fetchAppSettings()
      .then((data) => {
        if (!active) return;
        setItems(data.items || []);
        setEnvFile(data.env_file || "");
        setBackendAvailable(Boolean(data.backend_available));
        if (!data.backend_available) {
          setNotice("Server settings API is not configured yet. Values shown here are a safe UI scaffold only.");
        }
      })
      .catch((err) => active && setError(err.message || "Failed to load settings"))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  const groups = React.useMemo(() => BELGIUM.apiKeysConfig.groupItems(items), [items]);

  function updateDraft(key, value) {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setValidationResults((prev) => {
      if (!prev[key]) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
    setNotice("");
    setError("");
  }

  function validationValues() {
    const values = {};
    Object.entries(draft).forEach(([key, value]) => {
      if (String(value ?? "").trim() !== "") values[key] = value;
    });
    return values;
  }

  function mergeValidationResults(results) {
    setValidationResults((prev) => {
      const next = { ...prev };
      (results || []).forEach((result) => {
        next[result.key] = result;
      });
      return next;
    });
  }

  async function handleValidate(key) {
    setValidatingKey(key);
    setError("");
    setNotice("");
    try {
      const data = await BELGIUM.settingsApi.validateAppSettings([key], validationValues());
      mergeValidationResults(data.results || []);
      if (!data.backend_available) {
        setNotice("Validation not wired yet on the server.");
      }
    } catch (err) {
      setError(err.message || "Failed to validate setting");
    } finally {
      setValidatingKey("");
    }
  }

  async function handleValidateAll() {
    const keys = items.filter((item) => item.can_validate).map((item) => item.key);
    if (!keys.length) return;
    setValidatingAll(true);
    setError("");
    setNotice("");
    try {
      const data = await BELGIUM.settingsApi.validateAppSettings(keys, validationValues());
      mergeValidationResults(data.results || []);
      if (!data.backend_available) {
        setNotice("Validation not wired yet on the server.");
      } else {
        const summary = data.summary || {};
        setNotice(
          `Validation complete: ${summary.valid || 0} valid, ${summary.invalid || 0} invalid, ${summary.missing || 0} missing.`
        );
      }
    } catch (err) {
      setError(err.message || "Failed to validate settings");
    } finally {
      setValidatingAll(false);
    }
  }

  async function handleSave() {
    const values = {};
    Object.entries(draft).forEach(([key, value]) => {
      if (String(value ?? "").trim() !== "") values[key] = value;
    });
    if (!Object.keys(values).length) {
      setError("Enter at least one value to update.");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const data = await BELGIUM.settingsApi.saveAppSettings(values);
      setItems(data.items || items);
      setEnvFile(data.env_file || envFile);
      setDraft({});
      setNotice("Settings saved to .env file successfully. Pipeline runs will use updated environment values.");
    } catch (err) {
      setError(err.message || "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="app-page api-keys-page">
      <div className="api-keys-shell app-card rounded-xl">
        <div className="api-keys-header">
          <div>
            <h1 className="api-keys-title">API Keys & Tokens</h1>
            <p className="api-keys-subtitle">
              Paste only the values you want to change. Existing secrets stay hidden.
            </p>
          </div>
          <button
            type="button"
            className="app-button app-btn-secondary api-keys-header-btn"
            onClick={handleValidateAll}
            disabled={saving || loading || validatingAll || Boolean(validatingKey)}
          >
            <BELGIUM.Icon name={validatingAll ? "refresh" : "check"} size={14} className={validatingAll ? "animate-spin" : ""} />
            {validatingAll ? "Validating…" : "Validate Keys"}
          </button>
        </div>

        <div className="api-keys-body">
          {loading ? (
            <div className="api-keys-loading">Loading settings…</div>
          ) : (
            <div className="api-keys-sections">
              {Object.entries(groups).map(([group, groupItems]) => (
                <section key={group} className="api-keys-section">
                  <h2 className="api-keys-section-title">{group}</h2>
                  <div className="api-keys-grid">
                    {groupItems.map((item) => (
                      <div key={item.key} className="api-key-card">
                        <div className="api-key-card-head">
                          <div className="min-w-0">
                            <div className="api-key-card-label">{item.label}</div>
                            <div className="api-key-card-env">{item.key}</div>
                          </div>
                          <div className="api-key-card-badges">
                            <ApiKeyValidationBadge result={validationResults[item.key]} />
                            <ApiKeySetBadge isSet={Boolean(item.is_set)} />
                          </div>
                        </div>
                        {item.hint && <p className="api-key-card-hint">{item.hint}</p>}
                        <p className="api-key-card-current">
                          Current: {item.masked_value || "Not configured"}
                        </p>
                        <input
                          type="password"
                          className="app-input api-key-card-input rounded-lg px-3 py-2 text-sm"
                          value={draft[item.key] || ""}
                          onChange={(event) => updateDraft(item.key, event.target.value)}
                          placeholder="Paste new value"
                          autoComplete="off"
                        />
                        <div className="api-key-card-actions">
                          <span className="api-key-card-detail">
                            {validationResults[item.key]?.detail || ""}
                          </span>
                          {item.can_validate && (
                            <button
                              type="button"
                              className="app-button app-btn-secondary api-key-validate-btn"
                              onClick={() => handleValidate(item.key)}
                              disabled={saving || validatingAll || validatingKey === item.key}
                            >
                              <BELGIUM.Icon
                                name={validatingKey === item.key ? "refresh" : "check"}
                                size={13}
                                className={validatingKey === item.key ? "animate-spin" : ""}
                              />
                              {validatingKey === item.key ? "Checking" : "Validate"}
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>

        <div className="api-keys-footer">
          {envFile && <div className="api-keys-env-file">Saving to: {envFile}</div>}
          {!backendAvailable && (
            <div className="api-keys-notice">Server settings API is not configured yet.</div>
          )}
          {error && <div className="api-keys-error">{error}</div>}
          {notice && <div className="api-keys-success">{notice}</div>}
          <div className="api-keys-footer-actions">
            <button
              type="button"
              className="app-button app-btn-secondary api-keys-footer-btn"
              onClick={handleValidateAll}
              disabled={saving || loading || validatingAll || Boolean(validatingKey)}
            >
              <BELGIUM.Icon name={validatingAll ? "refresh" : "check"} size={15} className={validatingAll ? "animate-spin" : ""} />
              {validatingAll ? "Validating…" : "Validate Keys"}
            </button>
            <button
              type="button"
              className="app-button app-btn-secondary api-keys-footer-btn"
              onClick={() => BELGIUM.navigateToPage("projects")}
            >
              Back
            </button>
            <button
              type="button"
              className="app-button app-button-primary api-keys-footer-btn text-white"
              onClick={handleSave}
              disabled={saving || loading}
            >
              {saving ? "Saving…" : "Save Settings"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
