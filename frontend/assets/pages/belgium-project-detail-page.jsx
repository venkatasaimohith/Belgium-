window.BELGIUM = window.BELGIUM || {};

BELGIUM.BelgiumProjectDetailPage = function BelgiumProjectDetailPage({ user, jobId, onBack }) {
  const [selectedJob, setSelectedJob] = React.useState(null);
  const [progress, setProgress] = React.useState(null);
  const [events, setEvents] = React.useState([]);
  const [jobFlow, setJobFlow] = React.useState(null);
  const [agents, setAgents] = React.useState([]);
  const [templates, setTemplates] = React.useState([]);
  const [error, setError] = React.useState("");
  const [processBusy, setProcessBusy] = React.useState(false);
  const [stopBusy, setStopBusy] = React.useState(false);
  const [downloadBusy, setDownloadBusy] = React.useState(false);
  const [showFlowModal, setShowFlowModal] = React.useState(false);
  const [showMapModal, setShowMapModal] = React.useState(false);
  const [showLogsModal, setShowLogsModal] = React.useState(false);
  const [records, setRecords] = React.useState([]);
  const [recordsCount, setRecordsCount] = React.useState(0);
  const [showSidebar, setShowSidebar] = React.useState(true);

  async function loadRecords() {
    if (!jobId) return;
    try {
      const data = await BELGIUM.belgiumApi.getJobRecords(jobId);
      setRecords(data.records || []);
      setRecordsCount(Number(data.count) || (data.records || []).length);
    } catch (err) {
      setError(err.message || "Failed to load address records");
    }
  }

  async function loadTemplates() {
    try {
      const data = await BELGIUM.belgiumApi.listFlowTemplates();
      setTemplates(data.templates || []);
    } catch (_) {}
  }

  async function loadSelected() {
    if (!jobId) return;
    try {
      const [job, progressData, eventData, flowData, recordsData] = await Promise.all([
        BELGIUM.belgiumApi.getJob(jobId),
        BELGIUM.belgiumApi.getProgress(jobId).catch(() => null),
        BELGIUM.belgiumApi.getEvents(jobId).catch(() => ({ events: [] })),
        BELGIUM.belgiumApi.getJobFlow(jobId).catch(() => null),
        BELGIUM.belgiumApi.getJobRecords(jobId),
      ]);
      const mergedJob = job ? {
        ...job,
        model_mode: "rf_model",
        ...(progressData?.status ? { status: progressData.status } : {}),
        ...(progressData?.current_agent ? { current_agent: progressData.current_agent } : {}),
        ...(progressData?.progress_pct !== undefined ? { progress_pct: progressData.progress_pct } : {}),
      } : job;
      setSelectedJob(mergedJob);
      setProgress(progressData);
      setEvents(eventData.events || []);
      setJobFlow(flowData);
      setRecords(recordsData.records || []);
      setRecordsCount(Number(recordsData.count) || (recordsData.records || []).length);
      setError(mergedJob?.error || "");
    } catch (err) {
      // Keep the last good data visible, but report failed refreshes.
      setError(err.message || "Failed to load project");
    }
  }

  React.useEffect(() => {
    BELGIUM.belgiumApi.listAgents().then((data) => setAgents(data.agents || [])).catch(() => {});
    loadTemplates();
    loadSelected();
  }, [jobId]);

  React.useEffect(() => {
    if (!jobId || showFlowModal) return undefined;
    const id = setInterval(() => {
      loadSelected().catch(() => {});
    }, 3000);
    return () => clearInterval(id);
  }, [jobId, showFlowModal]);

  async function processProject(resume = false) {
    if (!selectedJob || !BELGIUM.belgiumApi.canProcess(selectedJob.status)) return;
    setProcessBusy(true);
    setError("");
    try {
      await (resume === true ? BELGIUM.belgiumApi.resumeJob(jobId) : BELGIUM.belgiumApi.processJob(jobId));
      await loadSelected();
    } catch (err) {
      setError(err.message || "Failed to start processing");
    } finally {
      setProcessBusy(false);
    }
  }

  async function stopProject() {
    if (!selectedJob || !BELGIUM.belgiumApi.canStop(selectedJob.status)) return;
    if (!window.confirm("Force stop this pipeline? You can re-process after it stops.")) return;
    setStopBusy(true);
    setError("");
    try {
      await BELGIUM.belgiumApi.stopJob(jobId);
      await loadSelected();
    } catch (err) {
      setError(err.message || "Failed to stop processing");
    } finally {
      setStopBusy(false);
    }
  }

  async function downloadOutput(outputType) {
    if (!selectedJob) return;
    setDownloadBusy(true);
    setError("");
    try {
      const selectedYear = selectedJob.streetview_year || selectedJob.options?.streetview_year || "latest";
      await BELGIUM.belgiumApi.downloadOutput(selectedJob.job_id, outputType, selectedYear);
    } catch (err) {
      setError(err.message || "Download failed");
    } finally {
      setDownloadBusy(false);
    }
  }

  const sourceFile = selectedJob ? BELGIUM.jobDisplay.sourceFile(selectedJob) : "Belgium Project";
  const isProcessing = selectedJob && BELGIUM.jobDisplay.isProcessing(selectedJob.status);
  const displayProgress = progress?.progress_pct ?? BELGIUM.jobDisplay.progressPercent(selectedJob || {});
  const totalTimeText = BELGIUM.pipelineDisplay.pipelineTotalTimeText(selectedJob, progress);
  const flowName = React.useMemo(() => {
    const templateId = jobFlow?.template_id;
    const template = (templates || []).find((item) => item.id === templateId);
    if (template?.name) return template.name;
    if (jobFlow?.fallback) return "Belgium Default Flow";
    return templateId ? "Custom Flow" : "Belgium Default Flow";
  }, [jobFlow, templates]);

  if (!selectedJob && !error) {
    return (
      <div className="app-page project-detail-page flex items-center justify-center">
        <BELGIUM.Spinner size={24} />
        <span className="ml-2 text-sm" style={{ color: "var(--app-text-soft)" }}>
          Loading Belgium Project…
        </span>
      </div>
    );
  }

  if (!selectedJob && error) {
    return (
      <div className="app-page project-detail-page flex items-center justify-center p-6">
        <BELGIUM.EmptyState
          icon={<BELGIUM.Icon name="x" size={28} />}
          title="Belgium Project Not Found"
          description={error}
          action={
            <button type="button" className="app-button app-button-primary rounded-lg px-4 py-2 text-white" onClick={onBack}>
              Back to Projects
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div className="app-page project-detail-page">
      <BELGIUM.FlowSettingsModal
        open={showFlowModal}
        onClose={() => setShowFlowModal(false)}
        job={selectedJob}
        jobFlow={jobFlow}
        agents={agents}
        templates={templates}
        events={events}
        flowName={flowName}
        loadWarning={jobFlow?.fallback ? "Using default flow fallback." : ""}
        onSaved={loadSelected}
        onTemplatesRefresh={loadTemplates}
      />

      <BELGIUM.BelgiumLogsHistoryModal
        open={showLogsModal}
        onClose={() => setShowLogsModal(false)}
        events={events}
      />

      <BELGIUM.Modal
        open={showMapModal}
        title="Belgium Map Spatial Preview"
        onClose={() => setShowMapModal(false)}
        footer={
          <button
            type="button"
            className="app-button app-btn-secondary rounded-lg px-4 py-2"
            onClick={() => setShowMapModal(false)}
          >
            Close
          </button>
        }
      >
        <BELGIUM.EmptyState
          icon={<BELGIUM.Icon name="map" size={28} />}
          title="Belgium Spatial Layer Preview"
          description="Displays KML boundaries and GeoPackage layers for Belgium spatial coordinates."
        />
      </BELGIUM.Modal>

      <header className="project-detail-header flex-shrink-0">
        <BELGIUM.Breadcrumbs
          items={[
            { label: "Projects", onClick: onBack },
            { label: sourceFile },
          ]}
        />

        <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate font-bold">{sourceFile}</h1>
              <BELGIUM.StatusBadge status={selectedJob.status} pulse={isProcessing} />
              
              {isProcessing && (
                <span className="project-live-progress tabular-nums">
                  <span className="project-live-dot" />
                  {displayProgress}%
                </span>
              )}
              {totalTimeText && (
                <span className="project-runtime tabular-nums">
                  {totalTimeText}
                  {isProcessing ? " · running" : ""}
                </span>
              )}
            </div>

            <div className="project-detail-meta">
              <span className="font-mono" title={selectedJob.job_id}>
                Project {String(selectedJob.job_id).slice(0, 12)}
              </span>
              <span className="inline-flex items-center gap-1">
                <BELGIUM.Icon name="user" size={12} />
                {selectedJob.created_by || user?.email || "Belgium User"}
              </span>
              <span>{recordsCount || BELGIUM.jobDisplay.recordCount(selectedJob)} records</span>
              <span className="project-flow-chip inline-flex items-center gap-1 truncate">
                <BELGIUM.Icon name="settings" size={12} />
                {flowName}
              </span>
              <span>
                Input: {selectedJob.input_type || "KML/GPKG"}
              </span>
            </div>
          </div>

          <BELGIUM.ProjectDetailActions
            job={selectedJob}
            onBack={onBack}
            onOpenFlow={() => setShowFlowModal(true)}
            onOpenMap={() => setShowMapModal(true)}
            onProcess={processProject}
            onResume={() => processProject(true)}
            onStop={stopProject}
            processBusy={processBusy}
            stopBusy={stopBusy}
            onDownload={downloadOutput}
            downloadBusy={downloadBusy}
          />
        </div>

        {error && (
          <div className="mt-2 rounded-lg border border-rose-300 bg-rose-50 p-2 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300">
            {error}
          </div>
        )}
      </header>

      <div className={`project-detail-grid flex-1 min-h-0 ${!showSidebar ? 'sidebar-collapsed' : ''}`}>
        <div className="project-detail-main app-card rounded-xl min-w-0">
          <BELGIUM.BelgiumRecordsTable
            job={selectedJob}
            records={records}
            onRefresh={loadRecords}
            onViewMap={() => setShowMapModal(true)}
            onOpenLogs={() => setShowLogsModal(true)}
            onToggleSidebar={() => setShowSidebar((prev) => !prev)}
            showSidebar={showSidebar}
          />
        </div>

        {showSidebar && (
          <aside className="project-detail-aside app-card rounded-xl">
            <BELGIUM.BelgiumProgressPanel
              job={selectedJob}
              progress={progress}
              jobFlow={jobFlow}
              agents={agents}
              variant="sidebar"
              onProcess={() => processProject(false)}
              processBusy={processBusy}
            />
          </aside>
        )}
      </div>
    </div>
  );
};
