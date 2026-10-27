window.BELGIUM = window.BELGIUM || {};

BELGIUM.apiKeysConfig = {
  definitions: [
    {
      key: "GOOGLE_MAPS_API_KEY",
      label: "Google Maps API Key",
      group: "Google",
      hint: "Google Maps & Street View panorama imagery API key.",
      can_validate: true,
    },
    {
      key: "GOOGLE_API_KEY",
      label: "Google Generic API Key",
      group: "Google",
      hint: "Shared Google Cloud & Gemini API service key.",
      can_validate: true,
    },
    {
      key: "OLLAMA_BASE_URL",
      label: "Ollama Base URL",
      group: "Ollama",
      hint: "Local Ollama server endpoint, e.g. http://127.0.0.1:11434.",
      can_validate: true,
    },
    {
      key: "OLLAMA_MODEL",
      label: "Ollama Model Name",
      group: "Ollama",
      hint: "Default model for local vision & mailbox detection (e.g. qwen2-vl).",
      can_validate: true,
    },
    {
      key: "OLLAMA_API_KEY",
      label: "Ollama API Key / Token",
      group: "Ollama",
      hint: "Optional authentication token for remote Ollama deployments.",
      can_validate: false,
    },
  ],

  getDefaultItems() {
    return BELGIUM.apiKeysConfig.definitions.map((field) => ({
      ...field,
      is_set: false,
      masked_value: "",
    }));
  },

  groupItems(items) {
    const grouped = {};
    (items || []).forEach((item) => {
      const group = item.group || "Google";
      if (!grouped[group]) grouped[group] = [];
      grouped[group].push(item);
    });
    return grouped;
  },
};
