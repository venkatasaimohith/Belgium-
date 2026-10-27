window.BELGIUM = window.BELGIUM || {};

BELGIUM.BelgiumDashboardPage = function BelgiumDashboardPage({ user, refreshKey = 0, onOpenJob, onOpenUpload }) {
  const [jobs, setJobs] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [searchQuery, setSearchQuery] = React.useState("");
  const [viewMode, setViewMode] = React.useState("cards");
  const [selectedJobIds, setSelectedJobIds] = React.useState(() => new Set());
  const [deleteBusy, setDeleteBusy] = React.useState(false);

  async function loadJobs() {
    setError("");
    try {
      const data = await BELGIUM.belgiumApi.listJobs();
      setJobs(data.jobs || []);
    } catch (err) {
      setError(err.message || "Failed to load projects");
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => {
    loadJobs();
  }, [refreshKey]);

  async function processProject(jobId, event) {
    if (event) event.stopPropagation();
    setError("");
    try {
      await BELGIUM.belgiumApi.processJob(jobId);
      await loadJobs();
    } catch (err) {
      setError(err.message || "Failed to start processing");
    }
  }

  async function stopProject(jobId, event) {
    if (event) event.stopPropagation();
    if (!window.confirm("Force stop this pipeline? You can re-process after it stops.")) return;
    setError("");
    try {
      await BELGIUM.belgiumApi.stopJob(jobId);
      await loadJobs();
    } catch (err) {
      setError(err.message || "Failed to stop processing");
    }
  }

  function toggleJobSelection(jobId, event) {
    if (event) event.stopPropagation();
    setSelectedJobIds((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  }

  function selectableFilteredJobs() {
    return filteredJobs.filter((job) => BELGIUM.belgiumApi.canDelete(job.status));
  }

  function toggleSelectAllFiltered(event) {
    if (event) event.stopPropagation();
    const selectable = selectableFilteredJobs();
    const selectableIds = selectable.map((job) => job.job_id);
    const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selectedJobIds.has(id));
    setSelectedJobIds((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        selectableIds.forEach((id) => next.delete(id));
      } else {
        selectableIds.forEach((id) => next.add(id));
      }
      return next;
    });
  }

  async function deleteProject(jobId, event) {
    if (event) event.stopPropagation();
    if (!window.confirm("Delete this project? This cannot be undone.")) return;
    setError("");
    setDeleteBusy(true);
    try {
      await BELGIUM.belgiumApi.deleteJob(jobId);
      setSelectedJobIds((prev) => {
        const next = new Set(prev);
        next.delete(jobId);
        return next;
      });
      await loadJobs();
    } catch (err) {
      setError(err.message || "Failed to delete project");
    } finally {
      setDeleteBusy(false);
    }
  }

  async function deleteSelectedProjects() {
    const ids = [...selectedJobIds];
    if (!ids.length) return;
    if (!window.confirm(`Delete ${ids.length} selected project${ids.length > 1 ? "s" : ""}? This cannot be undone.`)) {
      return;
    }
    setError("");
    setDeleteBusy(true);
    try {
      const result = await BELGIUM.belgiumApi.bulkDeleteJobs(ids);
      const deleted = new Set(result.deleted_job_ids || []);
      setSelectedJobIds((prev) => {
        const next = new Set(prev);
        deleted.forEach((id) => next.delete(id));
        return next;
      });
      if (result.errors?.length) {
        setError(
          result.errors.map((item) => `${item.job_id}: ${item.error}`).join("; ") ||
            "Some projects could not be deleted"
        );
      }
      await loadJobs();
    } catch (err) {
      setError(err.message || "Failed to delete selected projects");
    } finally {
      setDeleteBusy(false);
    }
  }

  const filteredJobs = jobs.filter((job) => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return true;
    const source = BELGIUM.jobDisplay.sourceFile(job).toLowerCase();
    return source.includes(query) || String(job.job_id).toLowerCase().includes(query);
  });

  const stats = BELGIUM.jobDisplay.dashboardStats(jobs);
  const jobSerialMap = React.useMemo(() => BELGIUM.jobDisplay.uploadSerialMap(jobs), [jobs]);
  const selectableJobs = selectableFilteredJobs();
  const selectedCount = selectedJobIds.size;
  const allFilteredSelected =
    selectableJobs.length > 0 && selectableJobs.every((job) => selectedJobIds.has(job.job_id));

  if (loading) {
    return (
      <div className="app-page">
        <div className="mb-2 flex items-center justify-between gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--app-text-soft)" }}>
              Belgium Studio
            </div>
            <h1 className="text-lg font-bold">Belgium Projects</h1>
          </div>
        </div>
        <div className="app-page-scroll">
          <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">
            {[1, 2, 3, 4].map((item) => (
              <div key={item} className="app-card app-stat rounded-xl">
                <div className="h-3 w-16 animate-pulse rounded bg-slate-300/40" />
                <div className="mt-2 h-7 w-12 animate-pulse rounded bg-slate-300/40" />
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app-page">
      <div className="mb-2 flex flex-shrink-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--app-text-soft)" }}>
            Belgium Studio
          </div>
          <h1 className="text-lg font-bold">Belgium Projects</h1>
        </div>
        <button
          type="button"
          onClick={onOpenUpload}
          className="app-button app-button-primary inline-flex items-center gap-2 self-start rounded-lg px-4 py-2 sm:self-auto"
        >
          <BELGIUM.Icon name="upload" size={15} /> Upload Project (KML / GPKG)
        </button>
      </div>

      {error && (
        <div className="mb-2 flex-shrink-0 rounded-lg border border-rose-300 bg-rose-50 p-2 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300">
          {error}
        </div>
      )}

      <div className="app-page-scroll">
        <div className="mb-3 grid grid-cols-2 gap-2 xl:grid-cols-4">
          <BELGIUM.DashboardStatCard label="Belgium Projects" value={stats.projects} hint="KML & GPKG Uploads" />
          <BELGIUM.DashboardStatCard
            label="Spatial Records"
            value={stats.records.toLocaleString()}
            hint="Parcels, Buildings & Assets"
          />
          <BELGIUM.DashboardStatCard label="Processing" value={stats.processing} hint="Active AI & GIS pipelines" />
          <BELGIUM.DashboardStatCard label="Completed" value={stats.completed} hint="Ready KML & GPKG outputs" />
        </div>

        <div className="app-card mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl p-2 sm:p-3">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <div className="relative min-w-full max-w-xl flex-1 sm:min-w-[260px]">
              <span
                className="absolute left-3 top-1/2 -translate-y-1/2"
                style={{ color: "var(--app-text-soft)" }}
              >
                <BELGIUM.Icon name="search" size={15} />
              </span>
              <input
                type="search"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search Belgium projects by name or ID…"
                aria-label="Search projects"
                className="app-input w-full rounded-lg py-2.5 pl-9 pr-4"
              />
            </div>
            {filteredJobs.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <label className="dashboard-select-all inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm">
                  <input
                    type="checkbox"
                    checked={allFilteredSelected}
                    onChange={toggleSelectAllFiltered}
                    disabled={!selectableJobs.length || deleteBusy}
                  />
                  <span>Select all</span>
                </label>
                <button
                  type="button"
                  className="app-button dashboard-delete-selected inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm"
                  onClick={deleteSelectedProjects}
                  disabled={!selectedCount || deleteBusy}
                >
                  <BELGIUM.Icon name="trash" size={14} />
                  Delete selected{selectedCount ? ` (${selectedCount})` : ""}
                </button>
              </div>
            )}
          </div>
          <div
            className="flex w-full items-center gap-1 rounded-lg border p-1 sm:w-auto"
            style={{ background: "var(--app-surface-soft)", borderColor: "var(--app-border)" }}
            role="tablist"
            aria-label="View mode"
          >
            <button
              type="button"
              role="tab"
              aria-selected={viewMode === "cards"}
              onClick={() => setViewMode("cards")}
              className={`app-button flex flex-1 items-center justify-center gap-1.5 rounded px-3 py-1.5 text-sm sm:flex-none ${
                viewMode === "cards" ? "shadow" : ""
              }`}
              style={
                viewMode === "cards"
                  ? { background: "var(--app-surface)", color: "var(--app-text)" }
                  : { background: "transparent", color: "var(--app-text-soft)" }
              }
            >
              <BELGIUM.Icon name="grid" size={14} /> Cards
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={viewMode === "table"}
              onClick={() => setViewMode("table")}
              className={`app-button flex flex-1 items-center justify-center gap-1.5 rounded px-3 py-1.5 text-sm sm:flex-none ${
                viewMode === "table" ? "shadow" : ""
              }`}
              style={
                viewMode === "table"
                  ? { background: "var(--app-surface)", color: "var(--app-text)" }
                  : { background: "transparent", color: "var(--app-text-soft)" }
              }
            >
              <BELGIUM.Icon name="table" size={14} /> Table
            </button>
          </div>
        </div>

        {filteredJobs.length === 0 ? (
          <div className="app-card rounded-xl p-8 text-center">
            <p className="mb-4" style={{ color: "var(--app-text-soft)" }}>
              {searchQuery ? "No matching Belgium projects." : "No Belgium projects uploaded yet."}
            </p>
            {!searchQuery && (
              <button
                type="button"
                onClick={onOpenUpload}
                className="app-button app-button-primary inline-flex items-center gap-2 rounded-lg px-5 py-2.5"
              >
                <BELGIUM.Icon name="upload" size={16} /> Upload KML or GPKG File
              </button>
            )}
          </div>
        ) : viewMode === "cards" ? (
          <div className="grid grid-cols-1 gap-3 pb-2 md:grid-cols-2 xl:grid-cols-3">
            {filteredJobs.map((job) => {
              const jobId = job.id || job.job_id;
              const isProcessing = BELGIUM.jobDisplay.isProcessing(job.status);
              const progress = BELGIUM.jobDisplay.progressPercent(job);
              const recordCount = BELGIUM.jobDisplay.recordCount(job);
              const canDelete = BELGIUM.belgiumApi.canDelete(job.status);
              const isSelected = selectedJobIds.has(jobId);
              const serialNo = jobSerialMap.get(jobId) || 0;

              return (
                <article
                  key={jobId}
                  className={`app-card app-card-hover min-w-0 cursor-pointer rounded-xl p-4 ${
                    isSelected ? "dashboard-card-selected" : ""
                  }`}
                  onClick={() => onOpenJob(jobId)}
                >
                  <div className="mb-3 flex items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="dashboard-serial-badge tabular-nums" title={`Upload #${serialNo}`}>
                        {serialNo}
                      </span>
                      <label
                        className="dashboard-card-checkbox"
                        onClick={(event) => event.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          disabled={!canDelete || deleteBusy}
                          onChange={(event) => toggleJobSelection(jobId, event)}
                          aria-label={`Select ${BELGIUM.jobDisplay.sourceFile(job)}`}
                        />
                      </label>
                      <BELGIUM.StatusBadge status={job.status} pulse={isProcessing} />
                    </div>
                    <span
                      className="font-mono text-xs"
                      style={{ color: "var(--app-text-soft)" }}
                      title={jobId}
                    >
                      Project {String(jobId).slice(0, 10)}
                    </span>
                  </div>

                  <h3 className="mb-2 truncate font-semibold" title={BELGIUM.jobDisplay.sourceFile(job)}>
                    {BELGIUM.jobDisplay.sourceFile(job)}
                  </h3>

                  {/* Model Engine Badge */}
                  <div className="mb-3 flex items-center gap-2">
                    <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/20 px-2 py-0.5 text-[11px] font-semibold text-emerald-300">
                      ⚡ RF-DETR Mailbox Bundle
                    </span>
                    <span className="rounded bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">
                      {job.input_type || 'KML/GPKG'}
                    </span>
                  </div>

                  <div
                    className="mb-4 flex min-w-0 flex-wrap items-center gap-3 text-xs"
                    style={{ color: "var(--app-text-soft)" }}
                  >
                    <span className="inline-flex min-w-0 items-center gap-1 truncate">
                      <BELGIUM.Icon name="user" size={13} /> {job.created_by || user.email}
                    </span>
                    <span className="inline-flex items-center gap-1 whitespace-nowrap">
                      <BELGIUM.Icon name="clock" size={13} /> {BELGIUM.jobDisplay.relativeTime(job.created_at)}
                    </span>
                  </div>
                  <div className="mb-3">
                    <div className="text-2xl font-bold">{recordCount}</div>
                    <div
                      className="text-xs uppercase tracking-wide"
                      style={{ color: "var(--app-text-soft)" }}
                    >
                      Spatial Features
                    </div>
                  </div>
                  <BELGIUM.ProgressBar
                    progress={progress}
                    isActive={isProcessing}
                    label={`${recordCount} features processed`}
                  />
                  <div className="mt-4 flex flex-wrap items-center gap-2" onClick={(event) => event.stopPropagation()}>
                    <button
                      type="button"
                      className="app-button app-btn-map inline-flex items-center gap-1 rounded px-3 py-1.5 text-xs"
                      onClick={() => onOpenJob(jobId)}
                    >
                      <BELGIUM.Icon name="map" size={14} /> Open
                    </button>
                    {BELGIUM.belgiumApi.canProcess(job.status) && (
                      <button
                        type="button"
                        className={`app-button inline-flex items-center gap-1 rounded px-3 py-1.5 text-xs text-white ${
                          job.status === "COMPLETED" || job.status === "FAILED"
                            ? "bg-amber-600 hover:bg-amber-700"
                            : "bg-blue-600 hover:bg-blue-700"
                        }`}
                        onClick={(event) => processProject(jobId, event)}
                      >
                        <BELGIUM.Icon
                          name={job.status === "FAILED" || job.status === "COMPLETED" ? "refresh" : "play"}
                          size={14}
                        />{" "}
                        {BELGIUM.belgiumApi.processActionLabel(job.status)}
                      </button>
                    )}
                    {BELGIUM.belgiumApi.canStop(job.status) && (
                      <button
                        type="button"
                        className="app-button inline-flex items-center gap-1 rounded bg-red-600 px-3 py-1.5 text-xs text-white hover:bg-red-700"
                        onClick={(event) => stopProject(jobId, event)}
                      >
                        <BELGIUM.Icon name="x" size={14} /> Force stop
                      </button>
                    )}
                    {canDelete && (
                      <button
                        type="button"
                        className="app-button dashboard-delete-btn inline-flex items-center gap-1 rounded px-3 py-1.5 text-xs"
                        onClick={(event) => deleteProject(jobId, event)}
                        disabled={deleteBusy}
                      >
                        <BELGIUM.Icon name="trash" size={14} /> Delete
                      </button>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <div className="app-card overflow-x-auto rounded-xl">
            <table className="w-full min-w-[920px] text-sm">
              <thead>
                <tr
                  className="border-b"
                  style={{ background: "var(--app-surface-soft)", borderColor: "var(--app-border)" }}
                >
                  {["", "#", "Project Name", "Model Engine", "Status", "Uploader", "Records", "Progress", "Created", "Actions"].map(
                    (heading) => (
                      <th
                        key={heading || "select"}
                        className={`px-4 py-3 text-xs font-semibold uppercase tracking-wide ${
                          heading === "Records" ? "text-right" : heading === "#" ? "text-center w-14" : "text-left"
                        }`}
                        style={{ color: "var(--app-text-soft)" }}
                      >
                        {!heading ? (
                          <input
                            type="checkbox"
                            checked={allFilteredSelected}
                            onChange={toggleSelectAllFiltered}
                            disabled={!selectableJobs.length || deleteBusy}
                            aria-label="Select all visible projects"
                          />
                        ) : (
                          heading
                        )}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody>
                {filteredJobs.map((job) => {
                  const jobId = job.id || job.job_id;
                  const isProcessing = BELGIUM.jobDisplay.isProcessing(job.status);
                  const progress = BELGIUM.jobDisplay.progressPercent(job);
                  const canDelete = BELGIUM.belgiumApi.canDelete(job.status);
                  const isSelected = selectedJobIds.has(jobId);
                  const serialNo = jobSerialMap.get(jobId) || 0;

                  return (
                    <tr
                      key={jobId}
                      className={`cursor-pointer border-b transition-colors hover:bg-[color:var(--app-surface-soft)] ${
                        isSelected ? "dashboard-row-selected" : ""
                      }`}
                      style={{ borderColor: "var(--app-border)" }}
                      onClick={() => onOpenJob(jobId)}
                    >
                      <td className="px-4 py-3" onClick={(event) => event.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          disabled={!canDelete || deleteBusy}
                          onChange={(event) => toggleJobSelection(jobId, event)}
                          aria-label={`Select ${BELGIUM.jobDisplay.sourceFile(job)}`}
                        />
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className="dashboard-serial-badge dashboard-serial-badge--table tabular-nums" title={`Upload #${serialNo}`}>
                          {serialNo}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-medium" title={BELGIUM.jobDisplay.sourceFile(job)}>
                          {BELGIUM.jobDisplay.sourceFile(job)}
                        </div>
                        <div className="font-mono text-xs" style={{ color: "var(--app-text-soft)" }}>
                          Project {String(jobId).slice(0, 10)}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/20 px-2 py-0.5 text-[11px] font-semibold text-emerald-300">
                          ⚡ RF-DETR Bundle
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <BELGIUM.StatusBadge status={job.status} pulse={isProcessing} />
                      </td>
                      <td className="px-4 py-3" style={{ color: "var(--app-text-soft)" }}>
                        {job.created_by || user.email}
                      </td>
                      <td className="px-4 py-3 text-right font-bold">
                        {BELGIUM.jobDisplay.recordCount(job)}
                      </td>
                      <td className="w-48 px-4 py-3">
                        <BELGIUM.ProgressBar progress={progress} isActive={isProcessing} compact />
                      </td>
                      <td className="px-4 py-3 text-xs" style={{ color: "var(--app-text-soft)" }}>
                        {BELGIUM.jobDisplay.relativeTime(job.created_at)}
                      </td>
                      <td className="px-4 py-3" onClick={(event) => event.stopPropagation()}>
                        <div className="flex items-center gap-1">
                          <button
                            type="button"
                            className="app-button rounded p-1.5 hover:bg-[color:var(--app-surface-soft)]"
                            title="Open"
                            onClick={() => onOpenJob(jobId)}
                          >
                            <BELGIUM.Icon name="map" size={15} />
                          </button>
                          {BELGIUM.belgiumApi.canProcess(job.status) && (
                            <button
                              type="button"
                              className="app-button rounded p-1.5 hover:bg-[color:var(--app-surface-soft)]"
                              title={BELGIUM.belgiumApi.processActionLabel(job.status)}
                              onClick={(event) => processProject(jobId, event)}
                            >
                              <BELGIUM.Icon
                                name={
                                  job.status === "FAILED" || job.status === "COMPLETED" ? "refresh" : "play"
                                }
                                size={15}
                              />
                            </button>
                          )}
                          {BELGIUM.belgiumApi.canStop(job.status) && (
                            <button
                              type="button"
                              className="app-button rounded p-1.5 text-red-500 hover:bg-[color:var(--app-surface-soft)]"
                              title="Force stop"
                              onClick={(event) => stopProject(jobId, event)}
                            >
                              <BELGIUM.Icon name="x" size={15} />
                            </button>
                          )}
                          {canDelete && (
                            <button
                              type="button"
                              className="app-button dashboard-delete-btn rounded p-1.5"
                              title="Delete"
                              onClick={(event) => deleteProject(jobId, event)}
                              disabled={deleteBusy}
                            >
                              <BELGIUM.Icon name="trash" size={15} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
