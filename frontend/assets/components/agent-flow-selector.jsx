window.BELGIUM = window.BELGIUM || {};

const AGENT_FLOW_HIDDEN_UI_AGENT_KEYS = new Set(["verification"]);

BELGIUM.AgentFlowSelector = function AgentFlowSelector(props) {
  const tokens = BELGIUM.useThemeTokens();
  const safeTemplates = Array.isArray(props.templates) ? props.templates : [];
  const safeAgents = Array.isArray(props.agents)
    ? props.agents.filter((agent) => !AGENT_FLOW_HIDDEN_UI_AGENT_KEYS.has(agent?.key))
    : [];
  const safeFlow = Array.isArray(props.flow) ? props.flow : [];
  const selectedTemplateId = props.selectedTemplateId || "";
  const loadWarning = props.loadWarning || "";
  const readOnly = Boolean(props.readOnly);
  const onTemplateChange = typeof props.onTemplateChange === "function" ? props.onTemplateChange : function () {};
  const onFlowChange = typeof props.onFlowChange === "function" ? props.onFlowChange : function () {};
  const onTemplateSaved = typeof props.onTemplateSaved === "function" ? props.onTemplateSaved : null;
  const onTemplateDeleted = typeof props.onTemplateDeleted === "function" ? props.onTemplateDeleted : null;

  const orderedTemplates = React.useMemo(() => {
    const systemDefault =
      safeTemplates.find((template) => template.is_system && template.is_default) ||
      safeTemplates.find((template) => template.name === "Default Belgium Flow") ||
      safeTemplates.find((template) => template.is_system);
    const custom = safeTemplates
      .filter((template) => !template.is_system)
      .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
    const otherSystem = safeTemplates.filter((template) => template.is_system && template !== systemDefault);
    return [...(systemDefault ? [systemDefault] : []), ...custom, ...otherSystem];
  }, [safeTemplates]);

  const selectedTemplate = orderedTemplates.find((template) => template && template.id === selectedTemplateId) || null;

  const [validation, setValidation] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [saveName, setSaveName] = React.useState("");
  const [saveError, setSaveError] = React.useState("");
  const [saveSuccess, setSaveSuccess] = React.useState("");
  const [showSave, setShowSave] = React.useState(false);
  const [templateOpen, setTemplateOpen] = React.useState(false);
  const templatePickerRef = React.useRef(null);

  const flowItems = React.useMemo(
    () =>
      safeFlow
        .filter((item) => item && item.key)
        .filter((item) => !AGENT_FLOW_HIDDEN_UI_AGENT_KEYS.has(item.key))
        .sort((a, b) => Number(a.order || 0) - Number(b.order || 0)),
    [safeFlow]
  );

  React.useEffect(() => {
    if (!templateOpen) return undefined;
    function handlePointerDown(event) {
      if (templatePickerRef.current && !templatePickerRef.current.contains(event.target)) {
        setTemplateOpen(false);
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [templateOpen]);

  const saveNameExists = React.useMemo(() => {
    const normalized = (saveName || "").trim().toLowerCase();
    if (!normalized) return false;
    return safeTemplates.some(
      (template) => template && (template.name || "").trim().toLowerCase() === normalized
    );
  }, [saveName, safeTemplates]);

  React.useEffect(() => {
    if (!saveSuccess) return undefined;
    const timeoutId = setTimeout(() => setSaveSuccess(""), 5000);
    return () => clearTimeout(timeoutId);
  }, [saveSuccess]);

  function updateFlow(nextFlow) {
    onFlowChange(Array.isArray(nextFlow) ? nextFlow : []);
    setValidation(null);
  }

  function toggleEnabled(key) {
    if (readOnly || !key) return;
    const item = safeFlow.find((row) => row && row.key === key);
    if (!item || item.locked) return;
    updateFlow(
      safeFlow.map((row) => (row && row.key === key ? { ...row, enabled: !row.enabled } : row))
    );
  }

  function canMoveUp(index) {
    if (readOnly || index <= 0) return false;
    if (flowItems[index]?.key === "kmz_extraction") return false;
    return true;
  }

  function canMoveDown(index) {
    if (readOnly || index >= flowItems.length - 1) return false;
    if (flowItems[index]?.key === "final_output") return false;
    if (flowItems[index + 1]?.key === "final_output") return false;
    return true;
  }

  function moveItem(index, direction) {
    if (readOnly) return;
    const targetIndex = index + direction;
    if (targetIndex < 0 || targetIndex >= flowItems.length) return;
    if (direction === -1 && !canMoveUp(index)) return;
    if (direction === 1 && !canMoveDown(index)) return;

    const keys = flowItems.map((item) => item.key);
    [keys[index], keys[targetIndex]] = [keys[targetIndex], keys[index]];

    updateFlow(
      safeFlow.map((item) => {
        if (!item || !item.key) return item;
        const newOrder = keys.indexOf(item.key);
        if (newOrder === -1) return item;
        return { ...item, order: newOrder + 1 };
      })
    );
  }

  async function validateCurrentFlow() {
    if (!safeFlow.length) return null;
    setBusy(true);
    try {
      const api = BELGIUM.belgiumApi || {};
      if (typeof api.validateFlow !== "function") {
        setSaveError("Flow validation is unavailable.");
        return null;
      }
      const result = await api.validateFlow(safeFlow);
      setValidation(result || null);
      if (result && result.valid && Array.isArray(result.normalized_flow)) {
        onFlowChange(result.normalized_flow);
      }
      return result;
    } catch (err) {
      setSaveError(err.message || "Flow validation failed.");
      return null;
    } finally {
      setBusy(false);
    }
  }

  function resetToDefault() {
    const defaultTemplate =
      orderedTemplates.find((t) => t && t.is_default) ||
      orderedTemplates.find((t) => t && t.name === "Default Belgium Flow") ||
      orderedTemplates[0];
    if (defaultTemplate) {
      onTemplateChange(defaultTemplate.id || "", Array.isArray(defaultTemplate.flow) ? defaultTemplate.flow : []);
    }
    setValidation(null);
    setSaveError("");
    setSaveSuccess("");
  }

  async function deleteTemplate(template) {
    if (!template || template.is_system) return;
    if (!window.confirm(`Delete custom template "${template.name}"?`)) return;

    setSaveError("");
    setSaveSuccess("");
    setBusy(true);
    try {
      const api = BELGIUM.belgiumApi || {};
      if (typeof api.deleteFlowTemplate !== "function") {
        setSaveError("Template delete is unavailable.");
        return;
      }
      await api.deleteFlowTemplate(template.id);
      if (onTemplateDeleted) {
        await onTemplateDeleted(template.id);
      } else if (selectedTemplateId === template.id) {
        resetToDefault();
      }
      setTemplateOpen(false);
      setSaveSuccess(`Custom template "${template.name}" deleted.`);
    } catch (err) {
      setSaveError(err.message || "Failed to delete template");
    } finally {
      setBusy(false);
    }
  }

  function templateCreatorLabel(template) {
    if (!template || template.is_system) return "System";
    return template.created_by || "Custom user";
  }

  function selectTemplate(template) {
    if (!template) return;
    onTemplateChange(template.id || "", Array.isArray(template.flow) ? template.flow : []);
    setTemplateOpen(false);
  }

  async function saveCustomTemplate() {
    const trimmedName = (saveName || "").trim();
    if (!trimmedName) return;
    if (saveNameExists) {
      setSaveError(`A flow template named '${trimmedName}' already exists. Choose a different name.`);
      return;
    }
    setSaveError("");
    setSaveSuccess("");
    setBusy(true);
    try {
      const api = BELGIUM.belgiumApi || {};
      if (typeof api.validateFlow !== "function" || typeof api.createFlowTemplate !== "function") {
        setSaveError("Template save is unavailable.");
        return;
      }
      const result = await api.validateFlow(safeFlow);
      if (!result || !result.valid) {
        setValidation(result || { valid: false, errors: ["Invalid agent flow"] });
        return;
      }
      const savedTemplate = await api.createFlowTemplate({
        name: trimmedName,
        description: "Custom belgium flow",
        flow: result.normalized_flow || safeFlow,
      });
      const templateFlow = Array.isArray(savedTemplate?.flow)
        ? savedTemplate.flow
        : result.normalized_flow || safeFlow;

      if (onTemplateSaved && savedTemplate && savedTemplate.id) {
        onTemplateSaved(savedTemplate, templateFlow);
      } else {
        onTemplateChange(savedTemplate?.id || "", templateFlow);
        onFlowChange(templateFlow);
      }

      setShowSave(false);
      setSaveName("");
      setSaveError("");
      setSaveSuccess(`Custom template "${trimmedName}" saved successfully.`);
    } catch (err) {
      setSaveSuccess("");
      setSaveError(err.message || "Failed to save template");
    } finally {
      setBusy(false);
    }
  }

  const agentLabel = (key) => {
    const api = BELGIUM.belgiumApi || {};
    if (typeof api.agentLabel === "function") return api.agentLabel(key, safeAgents);
    return key || "Unknown agent";
  };

  const agentDescription = (key) => {
    const api = BELGIUM.belgiumApi || {};
    if (typeof api.agentDescription === "function") return api.agentDescription(key, safeAgents);
    return "";
  };

  return (
    <div className="agent-flow-panel space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="upload-section-title">Agent Flow</h3>
        {!readOnly && safeFlow.length > 0 && (
          <div className="agent-flow-toolbar">
            <button
              type="button"
              className="app-button app-button-primary rounded-lg px-3 py-1.5 text-xs text-white"
              disabled={busy}
              onClick={validateCurrentFlow}
            >
              Validate Flow
            </button>
            <button
              type="button"
              className="app-button app-btn-secondary rounded-lg px-3 py-1.5 text-xs"
              disabled={busy}
              onClick={resetToDefault}
            >
              Reset to Default
            </button>
            <button
              type="button"
              className="app-button app-btn-secondary rounded-lg px-3 py-1.5 text-xs"
              disabled={busy}
              onClick={() => {
                setShowSave(true);
                setSaveError("");
                setSaveSuccess("");
              }}
            >
              Save as Custom Template
            </button>
          </div>
        )}
      </div>

      {loadWarning && <div className="flow-message flow-message-warning">{loadWarning}</div>}

      {!safeFlow.length && !loadWarning && (
        <div className="flow-message flow-message-warning">
          Default flow could not be loaded. Using default local flow.
        </div>
      )}

      {!readOnly && orderedTemplates.length > 0 && (
        <div className="flow-template-picker" ref={templatePickerRef}>
          <span className="text-sm" style={{ color: tokens.textSecondary }}>
            Flow Template
          </span>
          <button
            type="button"
            className={`flow-template-trigger ${templateOpen ? "is-open" : ""}`}
            onClick={() => setTemplateOpen((open) => !open)}
            disabled={busy}
          >
            {selectedTemplate ? (
              <span className="flow-template-trigger-row">
                <span className="flow-template-trigger-name">{selectedTemplate.name || "Unnamed template"}</span>
                <span className="flow-template-trigger-meta">{templateCreatorLabel(selectedTemplate)}</span>
                {!selectedTemplate.is_system && (
                  <span
                    role="button"
                    tabIndex={0}
                    className="flow-template-delete-inline"
                    title={`Delete ${selectedTemplate.name}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      deleteTemplate(selectedTemplate);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        event.stopPropagation();
                        deleteTemplate(selectedTemplate);
                      }
                    }}
                  >
                    <BELGIUM.Icon name="x" size={14} />
                  </span>
                )}
              </span>
            ) : (
              <span className="flow-template-trigger-placeholder">Select a flow template</span>
            )}
            <span className="flow-template-chevron" aria-hidden="true">
              ▾
            </span>
          </button>

          {templateOpen && (
            <div className="flow-template-menu" role="listbox">
              {orderedTemplates.map((template) => {
                const isSelected = template.id === selectedTemplateId;
                return (
                  <div
                    key={template.id || template.name || "template"}
                    className={`flow-template-option ${isSelected ? "is-selected" : ""}`}
                    role="option"
                    aria-selected={isSelected}
                  >
                    <button
                      type="button"
                      className="flow-template-option-select"
                      onClick={() => selectTemplate(template)}
                    >
                      <span className="flow-template-option-name">
                        {template.name || "Unnamed template"}
                        {template.is_system ? " (System)" : ""}
                      </span>
                      <span className="flow-template-option-user">{templateCreatorLabel(template)}</span>
                    </button>
                    {!template.is_system && (
                      <button
                        type="button"
                        className="flow-template-delete-inline"
                        title={`Delete ${template.name}`}
                        disabled={busy}
                        onClick={(event) => {
                          event.stopPropagation();
                          deleteTemplate(template);
                        }}
                      >
                        <BELGIUM.Icon name="x" size={14} />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      <div className="agent-flow-list">
        {flowItems.map((item, index) => {
          const isEnabled = item.enabled !== false;
          return (
          <div
            key={item.key}
            className={`agent-flow-row ${isEnabled ? "is-active" : "is-off"}`}
          >
            <span className={`agent-flow-index ${isEnabled ? "" : "is-off"}`}>{index + 1}</span>
            <div className="min-w-0">
              <div className="agent-flow-title">{agentLabel(item.key)}</div>
              <div className="agent-flow-meta">
                {item.key}
                {agentDescription(item.key) ? ` · ${agentDescription(item.key)}` : ""}
              </div>
            </div>
            <div className="agent-flow-actions">
              {item.locked && <BELGIUM.Badge tone="info">Locked</BELGIUM.Badge>}
              {item.required && <BELGIUM.Badge tone="warning">Required</BELGIUM.Badge>}
              {!isEnabled && !readOnly && <BELGIUM.Badge tone="default">Off</BELGIUM.Badge>}
              {!readOnly && (
                <>
                  <label className="flex items-center gap-1 text-xs" style={{ color: tokens.textSecondary }}>
                    <input
                      type="checkbox"
                      checked={isEnabled}
                      disabled={Boolean(item.locked)}
                      onChange={() => toggleEnabled(item.key)}
                    />
                    On
                  </label>
                  <button
                    type="button"
                    className="app-button app-btn-secondary rounded px-2 py-1 text-xs"
                    disabled={!canMoveUp(index)}
                    onClick={() => moveItem(index, -1)}
                    title="Move up"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="app-button app-btn-secondary rounded px-2 py-1 text-xs"
                    disabled={!canMoveDown(index)}
                    onClick={() => moveItem(index, 1)}
                    title="Move down"
                  >
                    ↓
                  </button>
                </>
              )}
            </div>
          </div>
        );
        })}
      </div>

      {saveSuccess && <div className="flow-message flow-message-success">{saveSuccess}</div>}

      {validation && (
        <div className={`flow-message ${validation.valid ? "flow-message-success" : "flow-message-error"}`}>
          {validation.valid ? (
            <p>Flow is valid.</p>
          ) : (
            <ul className="list-disc pl-5">
              {(validation.errors || []).map((err) => (
                <li key={err}>{err}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {showSave && !readOnly && (
        <div className="agent-flow-save-box">
          <label className="grid flex-1 gap-1">
            <span className="text-sm" style={{ color: tokens.textSecondary }}>
              Template Name
            </span>
            <input
              className="app-input rounded-lg px-3 py-2 text-sm"
              value={saveName}
              onChange={(e) => {
                setSaveName(e.target.value);
                setSaveError("");
                setSaveSuccess("");
              }}
              placeholder="My Custom Flow"
            />
            {saveNameExists && (
              <span className="text-xs text-rose-600 dark:text-rose-300">
                A template named &quot;{(saveName || "").trim()}&quot; already exists. Choose a different name.
              </span>
            )}
          </label>
          {saveError && <div className="mt-2 flow-message flow-message-error">{saveError}</div>}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              className="app-button app-button-primary rounded-lg px-3 py-1.5 text-xs text-white"
              disabled={busy || !(saveName || "").trim() || saveNameExists}
              onClick={saveCustomTemplate}
            >
              Save
            </button>
            <button
              type="button"
              className="app-button app-btn-secondary rounded-lg px-3 py-1.5 text-xs"
              onClick={() => {
                setShowSave(false);
                setSaveError("");
                setSaveSuccess("");
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
