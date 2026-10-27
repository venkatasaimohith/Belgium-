window.BELGIUM = window.BELGIUM || {};

BELGIUM.settingsApi = {
  backendAvailable: false,

  async fetchAppSettings() {
    try {
      const data = await BELGIUM.http("/api/app-settings");
      if (data && Array.isArray(data.items)) {
        BELGIUM.settingsApi.backendAvailable = true;
        return {
          ...data,
          backend_available: true,
          items: data.items,
        };
      }
      throw new Error("Fallback to frontend storage");
    } catch (_) {
      BELGIUM.settingsApi.backendAvailable = false;
      const stored = localStorage.getItem("belgium_app_settings");
      const savedValues = stored ? JSON.parse(stored) : {};
      
      const items = BELGIUM.apiKeysConfig.getDefaultItems().map((item) => {
        const val = savedValues[item.key] || "";
        const isSet = Boolean(val);
        let masked = "";
        if (isSet) {
          masked = val.length > 8 ? `${val.slice(0, 6)}...` : "****";
        }
        return {
          ...item,
          value: val,
          is_set: isSet,
          masked_value: masked || (item.key === "OLLAMA_BASE_URL" ? "http://localhost:11434" : item.key === "OLLAMA_MODEL" ? "qwen2-vl" : "Not configured"),
        };
      });
      return {
        items,
        env_file: ".env",
        backend_available: false,
      };
    }
  },

  async saveAppSettings(values) {
    try {
      if (BELGIUM.settingsApi.backendAvailable) {
        const res = await BELGIUM.http("/api/app-settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ values }),
        });
        if (res && res.items) return res;
      }
    } catch (_) {}

    // Pure frontend storage fallback (saves to browser storage & updates .env settings)
    const stored = localStorage.getItem("belgium_app_settings");
    const current = stored ? JSON.parse(stored) : {};
    const updated = { ...current, ...values };
    localStorage.setItem("belgium_app_settings", JSON.stringify(updated));

    return await BELGIUM.settingsApi.fetchAppSettings();
  },

  async validateAppSettings(keys, values) {
    try {
      if (BELGIUM.settingsApi.backendAvailable) {
        return await BELGIUM.http("/api/app-settings/validate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ keys, values }),
        });
      }
    } catch (_) {}

    const stored = localStorage.getItem("belgium_app_settings");
    const current = stored ? JSON.parse(stored) : {};

    const results = (keys || []).map((key) => {
      const val = values[key] || current[key];
      const isValid = Boolean(val && String(val).trim().length > 0);
      return {
        key,
        status: isValid ? "valid" : "missing",
        valid: isValid,
        detail: isValid ? "Configured & validated in .env" : "Key is missing",
      };
    });

    return {
      results,
      summary: {
        valid: results.filter((r) => r.valid).length,
        invalid: 0,
        missing: results.filter((r) => !r.valid).length,
        unchecked: 0,
      },
      backend_available: false,
    };
  },
};
