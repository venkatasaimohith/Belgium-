window.BELGIUM = window.BELGIUM || {};

BELGIUM.BelgiumProgressPanel = function BelgiumProgressPanel({
  job,
  progress,
  jobFlow,
  agents,
  variant = "sidebar",
  onProcess,
  processBusy,
}) {
  const [selectedViews, setSelectedViews] = React.useState(4);
  const [deduplicateEnabled, setDeduplicateEnabled] = React.useState(true);
  const [diagramMode, setDiagramMode] = React.useState("timeline");

  const data = progress && progress.stages ? progress : job;
  const jobStatus = job?.status || data?.status || "UPLOADED";
  const isLive = BELGIUM.belgiumApi.isProcessingStatus(jobStatus);
  const [clock, setClock] = React.useState(Date.now());

  React.useEffect(() => {
    setClock(Date.now());
    if (!isLive) return undefined;
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [isLive, job?.job_id]);

  const stepsDiagram = [
    { num: 1, title: "Input + Dedup", desc: "Agent 1 — group multi-unit dwellings", key: "agent1_dedup" },
    { num: 2, title: "Parcel", desc: "Agent 2 — download CadGIS parcel", key: "agent2_parcel" },
    { num: 3, title: "Street View Images", desc: "Agent 3 — best panorama, FOV 90 + 110", key: "agent3_image" },
    { num: 4, title: "Image Enhancement", desc: "Agent 4 — Real-ESRGAN 2× enhancement", key: "agent4_assign" },
    { num: 5, title: "Detect Mailboxes", desc: "Agent 5A RF-DETR + Agent 5B Florence-2 verification", key: "agent5_detect" },
    { num: 6, title: "Reject Neighbours", desc: "Agent 6 — target house and driveway ownership", key: "agent6_reject" },
    { num: 7, title: "Floor Count", desc: "Agent 7 — target facade floor estimation", key: "agent7_floor_count" },
    { num: 8, title: "Export Results", desc: "GPKG + KML output", key: "export_results" },
  ];

  const currentAgentKey = data?.current_agent || job?.current_agent || "agent1_dedup";
  const currentStepIndex = stepsDiagram.findIndex((s) => s.key === currentAgentKey);
  const stepCount = stepsDiagram.length;
  const stages = BELGIUM.pipelineDisplay.resolveStagesForTiming(job, progress);
  const stageByKey = stages.reduce((map, stage) => {
    if (stage?.name) map[stage.name] = stage;
    return map;
  }, {});
  const progressPct = Math.max(0, Math.min(100, Number(data?.progress_pct ?? job?.progress_pct) || 0));
  const elapsedMs = BELGIUM.pipelineDisplay.pipelineProcessingDurationMs(stages, isLive, clock);
  const remainingMs = BELGIUM.pipelineDisplay.estimateRemainingMs(stages, isLive, progressPct);
  const currentStep = stepsDiagram[Math.max(currentStepIndex, 0)];
  const elapsedText = elapsedMs == null ? "—" : BELGIUM.pipelineDisplay.formatDurationMs(elapsedMs);
  const remainingText = jobStatus === "COMPLETED"
    ? "Done"
    : isLive
      ? remainingMs == null ? "Estimating…" : BELGIUM.pipelineDisplay.formatDurationMs(remainingMs)
      : "—";

  return (
    <div className="pipeline-sidebar flex flex-col gap-4 p-4 text-slate-900 dark:text-slate-100 bg-white/70 dark:bg-slate-950/90 rounded-xl h-full overflow-y-auto">
      
      {/* Sidebar Header Title & Run Button */}
      <div className="flex flex-col gap-3 border-b border-slate-200 dark:border-slate-800 pb-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <h2 className="text-base font-bold text-slate-900 dark:text-white leading-tight">Validation Pipeline Monitor</h2>
            <p className="text-[11px] text-slate-600 dark:text-slate-400 mt-1">
              Live Agents 1–7 mailbox and floor-count pipeline monitor.
            </p>
          </div>
          <button
            type="button"
            title="Toggle Stepper vs Grid View"
            onClick={() => setDiagramMode(prev => prev === "timeline" ? "grid" : "timeline")}
            className="flex-shrink-0 rounded-lg border border-slate-300 dark:border-slate-800 bg-slate-100 dark:bg-slate-900 p-1.5 text-slate-600 dark:text-slate-400 hover:text-cyan-600 dark:hover:text-cyan-400 hover:border-slate-400 dark:hover:border-slate-700 text-xs"
          >
            <BELGIUM.Icon name={diagramMode === "timeline" ? "grid" : "list"} size={14} />
          </button>
        </div>

        <button
          type="button"
          disabled={isLive || processBusy}
          onClick={onProcess}
          className={`w-full inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-xs font-extrabold text-white shadow-lg transition ${
            isLive
              ? "bg-cyan-600/50 cursor-not-allowed"
              : "bg-cyan-500 hover:bg-cyan-400 shadow-cyan-500/20 active:scale-95"
          }`}
        >
          {isLive ? <BELGIUM.Spinner size={14} /> : <BELGIUM.Icon name="play" size={14} />}
          {isLive ? "Processing Pipeline…" : "▶ Start Validation Pipeline"}
        </button>
      </div>

      {/* Live timing and ETA summary */}
      <div className="rounded-xl border border-cyan-500/30 bg-slate-100/90 dark:bg-slate-900/90 p-3 shadow-inner">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
              {isLive && <span className="pipeline-live-dot pipeline-live-dot--inline" />}
              Pipeline timing
            </div>
            <div className="mt-1 truncate text-xs font-bold text-slate-900 dark:text-white">
              {isLive ? `Running: ${currentStep?.title || currentAgentKey}` : jobStatus === "COMPLETED" ? "Pipeline completed" : "Ready to run"}
            </div>
          </div>
          <span className={`rounded-full border px-2.5 py-0.5 text-[10px] font-bold ${
            isLive
              ? "animate-pulse border-cyan-500/30 bg-cyan-500/20 text-cyan-700 dark:text-cyan-300"
              : jobStatus === "COMPLETED"
                ? "border-emerald-500/30 bg-emerald-500/20 text-emerald-700 dark:text-emerald-300"
                : "border-slate-300 bg-slate-200 text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
          }`}>
            {isLive ? "● Live" : jobStatus === "COMPLETED" ? "✓ Complete" : "Idle"}
          </span>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2">
          <div className="rounded-lg border border-slate-200 bg-white/70 p-2 dark:border-slate-800 dark:bg-slate-950/60">
            <div className="text-[9px] uppercase tracking-wide text-slate-500">{isLive ? "Elapsed" : "Overall time"}</div>
            <div className="mt-0.5 text-sm font-extrabold tabular-nums text-slate-900 dark:text-white">{elapsedText}</div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-white/70 p-2 dark:border-slate-800 dark:bg-slate-950/60">
            <div className="text-[9px] uppercase tracking-wide text-slate-500">Est. remaining</div>
            <div className="mt-0.5 text-sm font-extrabold tabular-nums text-cyan-700 dark:text-cyan-300">{remainingText}</div>
          </div>
        </div>

        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-300 dark:bg-slate-800">
          <div className="h-full rounded-full bg-cyan-400 transition-all duration-500" style={{ width: `${progressPct}%` }} />
        </div>
        <div className="mt-1 flex justify-between text-[9px] font-semibold text-slate-500 dark:text-slate-400">
          <span>{Math.round(progressPct)}% complete</span>
          <span>{jobStatus === "COMPLETED" ? `${stepCount}/${stepCount} agents finished` : `Agent ${Math.max(currentStepIndex, 0) + 1} of ${stepCount}`}</span>
        </div>
      </div>

      {/* Agents 1–7 Workflow Pipeline Diagram / Timeline */}
      <div className="rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-100/60 dark:bg-slate-900/60 p-3.5">
        <div className="mb-2.5 flex items-center justify-between">
          <span className="text-[10px] font-bold uppercase tracking-wider text-slate-600 dark:text-slate-400">
            Mailbox Agent Pipeline
          </span>
          <span className="text-[10px] font-semibold text-cyan-600 dark:text-cyan-400">
            {isLive
              ? `Step ${Math.max(currentStepIndex, 0) + 1} of ${stepCount}`
              : jobStatus === "COMPLETED"
              ? `${stepCount}/${stepCount} Finished`
              : `${stepCount} Steps`}
          </span>
        </div>

        {diagramMode === "timeline" ? (
          /* Vertical Stepper Timeline View */
          <div className="relative pl-1 space-y-2.5">
            {stepsDiagram.map((step, idx) => {
              const stage = stageByKey[step.key];
              const stageStatus = String(stage?.status || "").toUpperCase();
              const isCompleted = stageStatus === "COMPLETED" || (jobStatus === 'COMPLETED' && stageStatus !== "FAILED");
              const isActive = stageStatus === "RUNNING" || (isLive && idx === currentStepIndex);
              const isLast = idx === stepsDiagram.length - 1;
              const durationText = BELGIUM.pipelineDisplay.stageDurationText(stage, isLive) || "—";
              const recordProgress = BELGIUM.pipelineDisplay.stageRecordProgress(stage);
              const phaseA = stage?.result?.agent5a_detect;
              const phaseB = stage?.result?.agent5b_verify;

              return (
                <div key={step.num} className="relative flex items-start gap-3 group">
                  {!isLast && (
                    <div
                      className={`absolute left-[11px] top-6 bottom-[-10px] w-0.5 transition-colors ${
                        isCompleted ? "bg-emerald-500/60" : isActive ? "bg-cyan-500/60" : "bg-slate-300 dark:bg-slate-800"
                      }`}
                    />
                  )}
                  <span
                    className={`relative z-10 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[11px] font-extrabold transition-all ${
                      isActive
                        ? "bg-cyan-400 text-slate-950 ring-4 ring-cyan-400/20 shadow-md shadow-cyan-400/40 animate-bounce"
                        : isCompleted
                        ? "bg-emerald-400 text-slate-950"
                        : "bg-slate-200 dark:bg-slate-800 text-slate-700 dark:text-slate-400 border border-slate-300 dark:border-slate-700"
                    }`}
                  >
                    {isCompleted ? "✓" : step.num}
                  </span>
                  <div className={`flex-1 min-w-0 rounded-lg p-2 transition border ${
                    isActive
                      ? "border-cyan-500/40 bg-cyan-500/10"
                      : isCompleted
                      ? "border-emerald-500/30 bg-emerald-500/10"
                      : "border-slate-200 dark:border-slate-800/60 bg-white/80 dark:bg-slate-950/40"
                  }`}>
                    <div className="flex items-center justify-between">
                      <span className={`font-bold text-xs ${isActive ? 'text-cyan-700 dark:text-cyan-300' : isCompleted ? 'text-emerald-700 dark:text-emerald-300' : 'text-slate-800 dark:text-slate-300'}`}>
                        {step.title}
                      </span>
                      <span className={`text-[9px] font-bold tabular-nums ${isActive ? "animate-pulse text-cyan-600 dark:text-cyan-400" : "text-slate-500 dark:text-slate-400"}`}>
                        {isActive ? `Running · ${durationText}` : durationText}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-500 dark:text-slate-400 mt-0.5 line-clamp-1">{step.desc}</div>
                    {recordProgress && (
                      <div className="mt-1 text-[10px] font-extrabold tabular-nums text-cyan-700 dark:text-cyan-300">
                        {recordProgress.done ?? 0}/{recordProgress.total} processed
                      </div>
                    )}
                    {step.key === "agent5_detect" && (phaseA || phaseB) && (
                      <div className="mt-1 grid grid-cols-2 gap-1 text-[9px] tabular-nums text-slate-600 dark:text-slate-400">
                        <span>5A RF: {phaseA?.done ?? 0}/{phaseA?.total ?? 0}</span>
                        <span>5B Florence: {phaseB?.done ?? 0}/{phaseB?.total ?? 0}</span>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          /* Grid View (3x3) */
          <div className="grid grid-cols-3 gap-1.5">
            {stepsDiagram.map((step, idx) => {
              const stage = stageByKey[step.key];
              const stageStatus = String(stage?.status || "").toUpperCase();
              const isCompleted = stageStatus === "COMPLETED" || (jobStatus === 'COMPLETED' && stageStatus !== "FAILED");
              const isActive = stageStatus === "RUNNING" || (isLive && idx === currentStepIndex);
              const durationText = BELGIUM.pipelineDisplay.stageDurationText(stage, isLive) || "—";
              const recordProgress = BELGIUM.pipelineDisplay.stageRecordProgress(stage);
              return (
                <div
                  key={step.num}
                  className={`flex flex-col items-center rounded-lg border p-1.5 text-center transition ${
                    isActive
                      ? "border-cyan-400 bg-cyan-500/20 shadow-md shadow-cyan-500/20 ring-1 ring-cyan-400"
                      : isCompleted
                      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                      : "border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-slate-950/60 text-slate-600 dark:text-slate-400"
                  }`}
                >
                  <span className={`flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold ${
                    isActive ? 'bg-cyan-400 text-slate-950' : isCompleted ? 'bg-emerald-400 text-slate-950' : 'bg-slate-300 dark:bg-slate-800 text-slate-800 dark:text-slate-300'
                  }`}>
                    {step.num}
                  </span>
                  <div className="mt-1 font-bold text-[10px] text-slate-800 dark:text-slate-200 line-clamp-1">{step.title}</div>
                  <div className="text-[8px] text-slate-500 dark:text-slate-400 line-clamp-1">{step.desc}</div>
                  <div className={`mt-1 text-[9px] font-bold tabular-nums ${isActive ? "text-cyan-600 dark:text-cyan-300" : "text-slate-500"}`}>
                    {isActive ? `Live ${durationText}` : durationText}
                  </div>
                  {recordProgress && (
                    <div className="text-[8px] font-extrabold tabular-nums text-cyan-700 dark:text-cyan-300">
                      {recordProgress.done ?? 0}/{recordProgress.total}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

    </div>
  );
};
