window.BELGIUM = window.BELGIUM || {};

BELGIUM.FlowSettingsModal = function FlowSettingsModal({
  open,
  onClose,
  job,
  jobFlow,
  agents,
  templates,
  events,
  flowName,
  loadWarning,
  onSaved,
  onTemplatesRefresh,
}) {
  const [deduplicateEnabled, setDeduplicateEnabled] = React.useState(true);
  const [useApiCache, setUseApiCache] = React.useState(true);
  const [streetviewYear, setStreetviewYear] = React.useState("latest");
  const [customStreetviewYear, setCustomStreetviewYear] = React.useState(String(new Date().getFullYear() - 1));
  
  const [flow, setFlow] = React.useState([]);
  const [selectedTemplateId, setSelectedTemplateId] = React.useState("");
  const [localTemplates, setLocalTemplates] = React.useState([]);
  const [dirty, setDirty] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [success, setSuccess] = React.useState("");

  const editable = job && (job.status === "UPLOADED" || job.status === "FAILED" || job.status === "COMPLETED");

  function sortTemplates(list) {
    const templatesList = Array.isArray(list) ? list.filter(Boolean) : [];
    const systemDefault =
      templatesList.find((template) => template.is_system && template.is_default) ||
      templatesList.find((template) => template.name === "Default Belgium Flow") ||
      templatesList.find((template) => template.is_system);
    const custom = templatesList
      .filter((template) => !template.is_system)
      .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
    const otherSystem = templatesList.filter((template) => template.is_system && template !== systemDefault);
    return [...(systemDefault ? [systemDefault] : []), ...custom, ...otherSystem];
  }

  React.useEffect(() => {
    if (!open) {
      setDirty(false);
      return;
    }

    setFlow(Array.isArray(jobFlow?.flow) ? jobFlow.flow : []);
    setSelectedTemplateId(jobFlow?.template_id || "");
    setDeduplicateEnabled(job?.deduplicate_enabled !== false);
    setUseApiCache((job?.use_api_cache ?? job?.options?.use_api_cache) !== false);
    
    const savedYear = String(job?.streetview_year ?? job?.options?.streetview_year ?? "latest");
    const standardYears = Array.from({ length: 12 }, (_, index) => String(new Date().getFullYear() - index));
    if (savedYear !== "latest" && !standardYears.includes(savedYear)) {
      setStreetviewYear("custom");
      setCustomStreetviewYear(savedYear);
    } else {
      setStreetviewYear(savedYear);
    }
    setLocalTemplates(sortTemplates(templates));
    setError("");
    setSuccess("");
  }, [open, job, jobFlow, templates]);

  function handleFlowChange(nextFlow) {
    setDirty(true);
    setSuccess("");
    setFlow(Array.isArray(nextFlow) ? nextFlow : []);
  }

  function handleTemplateChange(templateId, templateFlow) {
    setDirty(true);
    setSelectedTemplateId(templateId || "");
    setFlow(Array.isArray(templateFlow) ? templateFlow : []);
    setSuccess("");
    setError("");
  }

  function handleTemplateSaved(savedTemplate, templateFlow) {
    if (!savedTemplate || !savedTemplate.id) return;
    const flowSnapshot = Array.isArray(templateFlow)
      ? templateFlow
      : Array.isArray(savedTemplate.flow)
        ? savedTemplate.flow
        : [];
    setLocalTemplates((prev) => {
      const next = prev.filter((template) => template && template.id !== savedTemplate.id);
      return sortTemplates([...next, savedTemplate]);
    });
    setSelectedTemplateId(savedTemplate.id);
    setFlow(flowSnapshot);
    setDirty(true);
    if (typeof onTemplatesRefresh === "function") {
      onTemplatesRefresh().catch(() => {});
    }
  }

  async function handleTemplateDeleted(templateId) {
    setLocalTemplates((prev) => prev.filter((template) => template && template.id !== templateId));
    if (selectedTemplateId === templateId) {
      const remaining = sortTemplates(localTemplates.filter((template) => template && template.id !== templateId));
      const defaultTemplate =
        remaining.find((template) => template.is_system && template.is_default) ||
        remaining.find((template) => template.name === "Default Belgium Flow") ||
        remaining[0];
      if (defaultTemplate) {
        handleTemplateChange(
          defaultTemplate.id || "",
          Array.isArray(defaultTemplate.flow) ? defaultTemplate.flow : []
        );
      }
    }
    if (typeof onTemplatesRefresh === "function") {
      await onTemplatesRefresh();
    }
  }

  async function saveChanges(event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    if (!job) return;
    setBusy(true);
    setError("");
    setSuccess("");

    try {
      const safeFlow = Array.isArray(flow) ? flow : [];
      let validatedFlow = safeFlow;

      if (safeFlow.length && typeof BELGIUM.belgiumApi.validateFlow === "function") {
        const validation = await BELGIUM.belgiumApi.validateFlow(safeFlow);
        if (!validation?.valid) {
          setError((validation?.errors || ["Invalid validation flow"]).join("; "));
          setBusy(false);
          return;
        }
        validatedFlow = validation.normalized_flow || safeFlow;
      }

      const modelModeKey = "rf_model";
      const effectiveStreetviewYear = streetviewYear === "custom" ? customStreetviewYear : streetviewYear;
      if (
        effectiveStreetviewYear !== "latest" &&
        (!/^\d{4}$/.test(String(effectiveStreetviewYear)) || Number(effectiveStreetviewYear) < 2007 || Number(effectiveStreetviewYear) > new Date().getFullYear())
      ) {
        setError(`Street View year must be between 2007 and ${new Date().getFullYear()}.`);
        setBusy(false);
        return;
      }

      await BELGIUM.belgiumApi.updateJobFlow(job.job_id || job.id, {
        template_id: selectedTemplateId || null,
        flow: validatedFlow,
      });

      await BELGIUM.belgiumApi.updateJobOptions(job.job_id || job.id, {
        deduplicate_enabled: deduplicateEnabled,
        model_mode: modelModeKey,
        streetview_year: effectiveStreetviewYear,
        use_api_cache: useApiCache,
      });

      setDirty(false);
      if (typeof onSaved === "function") {
        await onSaved();
      }
      setSuccess("Validation flow & configuration saved successfully.");
    } catch (err) {
      setError(err.message || "Failed to save flow settings.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;

  return (
    <BELGIUM.Modal
      open={open}
      title="Belgium Validation Flow & Processing Configuration"
      size="lg"
      onClose={busy ? () => {} : onClose}
      footer={
        <>
          <button
            type="button"
            className="app-button app-btn-secondary rounded-lg px-4 py-2"
            onClick={onClose}
            disabled={busy}
          >
            Close
          </button>
          {editable && (
            <button
              type="button"
              className="app-button app-button-primary rounded-lg px-4 py-2 text-white"
              onClick={saveChanges}
              disabled={busy || !dirty}
            >
              {busy ? (
                <span className="inline-flex items-center gap-2">
                  <BELGIUM.Spinner size={14} /> Saving…
                </span>
              ) : !dirty ? (
                "Saved"
              ) : (
                "Save Changes"
              )}
            </button>
          )}
        </>
      }
    >
      <p className="mb-4 text-xs text-slate-400">
        Configure the Agents 1–7 mailbox and floor-count pipeline, Google Street View parameters, address deduplication, and AI model selection.
      </p>

      <div className="space-y-4 max-h-[70vh] overflow-y-auto pr-1">
        
        {/* Section 1: Validation Sequence Configurator */}
        <section className="rounded-xl border border-slate-800 bg-slate-900/80 p-4">
          <BELGIUM.ErrorBoundary fallback="Validation Flow UI failed to render.">
            <BELGIUM.AgentFlowSelector
              flow={flow}
              agents={agents || []}
              templates={localTemplates}
              selectedTemplateId={selectedTemplateId}
              onTemplateChange={handleTemplateChange}
              onTemplateSaved={handleTemplateSaved}
              onTemplateDeleted={handleTemplateDeleted}
              onFlowChange={handleFlowChange}
              loadWarning={loadWarning || ""}
              readOnly={!editable}
            />
          </BELGIUM.ErrorBoundary>
        </section>

        {/* Section 2: Google Street View Settings */}
        <section className="rounded-xl border border-slate-800 bg-slate-900/80 p-4 space-y-3">
          <div>
            <h3 className="text-sm font-bold text-white">Google Street View Settings</h3>
            <p className="text-xs text-slate-400">Choose the capture year used by Agent 3 and Street View image downloads.</p>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-400 mb-1">Provider</label>
            <div className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs font-medium text-slate-300">
              Google Street View
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-400 mb-1">Street View Capture Year</label>
            <select
              value={streetviewYear}
              onChange={(e) => {
                setStreetviewYear(e.target.value);
                setDirty(true);
              }}
              className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs font-medium text-slate-200 focus:outline-none focus:border-cyan-500"
            >
              <option value="latest">Latest Available Imagery (Auto / Most Recent)</option>
              {Array.from({ length: 12 }, (_, index) => new Date().getFullYear() - index).map((year) => (
                <option key={year} value={String(year)}>{year} (Historical Imagery)</option>
              ))}
              <option value="custom">Custom Specific Year…</option>
            </select>
            {streetviewYear === "custom" && (
              <input
                type="number"
                min="2007"
                max={new Date().getFullYear()}
                value={customStreetviewYear}
                onChange={(e) => {
                  setCustomStreetviewYear(e.target.value);
                  setDirty(true);
                }}
                className="mt-2 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs font-medium text-slate-200 focus:outline-none focus:border-cyan-500"
                placeholder="Enter year, e.g. 2018"
              />
            )}
            <p className="mt-1.5 text-[10px] text-slate-500">
              A historical year uses panoramas whose Google metadata date matches that year. Sites without a match are marked as unavailable for that year.
            </p>
          </div>


        </section>

        {/* Section 3: Processing & AI Model Options */}
        <section className="rounded-xl border border-slate-800 bg-slate-900/80 p-4 space-y-3">
          <div>
            <h3 className="text-sm font-bold text-white">Processing & AI Model Options</h3>
            <p className="text-xs text-slate-400">API cache, address deduplication, and mailbox detection model selection.</p>
          </div>

          <label className={`flex items-start gap-2.5 cursor-pointer rounded-lg border p-3 ${
            useApiCache ? "border-emerald-700/70 bg-emerald-950/30" : "border-amber-700/70 bg-amber-950/20"
          }`}>
            <input
              type="checkbox"
              checked={useApiCache}
              onChange={(e) => {
                setUseApiCache(e.target.checked);
                setDirty(true);
              }}
              className="mt-0.5 accent-emerald-500"
            />
            <div>
              <div className="text-xs font-bold text-slate-200">
                Use 30-Day API Cache {useApiCache ? "✓" : "(Off)"}
              </div>
              <p className="text-[10px] text-slate-400">
                {useApiCache
                  ? "Reuse fresh Redis/PostgreSQL API responses to reduce cost and run time."
                  : "Always call live APIs for this run; cached responses will not be read or written."}
              </p>
            </div>
          </label>

          <label className="flex items-start gap-2.5 cursor-pointer rounded-lg border border-slate-800 bg-slate-950 p-3">
            <input
              type="checkbox"
              checked={deduplicateEnabled}
              onChange={(e) => {
                setDeduplicateEnabled(e.target.checked);
                setDirty(true);
              }}
              className="mt-0.5 accent-cyan-500"
            />
            <div>
              <div className="text-xs font-bold text-slate-200">Deduplicate Addresses Before Processing</div>
              <p className="text-[10px] text-slate-400">
                Multiple identical address records will be processed once and the result will be mapped back to all original records.
              </p>
            </div>
          </label>

          <div>
            <label className="block text-xs font-semibold text-slate-400 mb-1">Mailbox Detection AI Model</label>
            <div className="grid grid-cols-1 gap-2">
              <div className="flex flex-col items-start rounded-xl border border-cyan-400 bg-cyan-500/20 p-2.5 text-cyan-300">
                <div className="font-bold text-xs">RF-DETR Large Mailbox Bundle</div>
                <div className="text-[9px] opacity-75">last_ema.pth · mailbox · confidence 0.40</div>
              </div>
            </div>
          </div>
        </section>

      </div>

      {success && (
        <div className="mt-3 rounded-lg border border-emerald-300 bg-emerald-50 p-2.5 text-xs text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200">
          {success}
        </div>
      )}

      {error && (
        <div className="mt-3 rounded-lg border border-rose-300 bg-rose-50 p-2.5 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300">
          {error}
        </div>
      )}
    </BELGIUM.Modal>
  );
};

BELGIUM.FlowReadonlyModal = BELGIUM.FlowSettingsModal;
