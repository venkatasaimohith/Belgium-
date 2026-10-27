window.BELGIUM = window.BELGIUM || {};

const HIDDEN_UI_AGENT_KEYS = new Set(["verification"]);

BELGIUM.pipelineDisplay = {
  formatDurationMs(ms) {
    const safeMs = Math.max(0, Number(ms) || 0);
    if (safeMs > 0 && safeMs < 1000) return "<1s";
    const totalSeconds = Math.floor(safeMs / 1000);
    if (totalSeconds < 60) return `${totalSeconds}s`;
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    if (minutes < 60) return `${minutes}m ${seconds}s`;
    const hours = Math.floor(minutes / 60);
    const remMinutes = minutes % 60;
    return remMinutes > 0 ? `${hours}h ${remMinutes}m` : `${hours}h`;
  },

  formatProcessingTime(ms) {
    const safeMs = Math.max(0, Math.floor(Number(ms) || 0));
    const hours = Math.floor(safeMs / 3600000);
    const minutes = Math.floor((safeMs % 3600000) / 60000);
    const seconds = Math.floor((safeMs % 60000) / 1000);
    if (hours >= 1) {
      return `${hours}h ${minutes}m ${seconds}s`;
    }
    const centiseconds = Math.floor((safeMs % 1000) / 10);
    return `${minutes}m ${seconds}s ${String(centiseconds).padStart(2, "0")}ms`;
  },

  resolveStagesForTiming(job, progress) {
    const jobStages = job?.stages || [];
    const progressStages = progress?.stages || [];
    if (!jobStages.length) return progressStages;
    if (!progressStages.length) return jobStages;

    const progressMap = {};
    progressStages.forEach((stage) => {
      if (stage?.name) progressMap[stage.name] = stage;
    });

    return jobStages.map((stage) => {
      const liveStage = progressMap[stage.name];
      return {
        ...stage,
        status: liveStage?.status || stage.status,
        progress: liveStage?.progress ?? stage.progress,
        result: liveStage?.result || stage.result || {},
        error_message: liveStage?.error_message || stage.error_message || "",
        started_at: stage.started_at || liveStage?.started_at || null,
        completed_at: stage.completed_at || liveStage?.completed_at || null,
      };
    });
  },

  pipelineProcessingDurationMs(stages, isLive, nowMs = Date.now()) {
    const rows = Array.isArray(stages) ? stages : [];
    let earliestStart = null;
    let latestEnd = null;

    rows.forEach((stage) => {
      const started = stage?.started_at ? new Date(stage.started_at).getTime() : null;
      const completed = stage?.completed_at ? new Date(stage.completed_at).getTime() : null;
      if (started != null) {
        earliestStart = earliestStart == null ? started : Math.min(earliestStart, started);
      }
      if (completed != null) {
        latestEnd = latestEnd == null ? completed : Math.max(latestEnd, completed);
      }
    });

    if (earliestStart == null) {
      return isLive ? 0 : null;
    }

    if (isLive) {
      return Math.max(0, nowMs - earliestStart);
    }

    if (latestEnd != null && latestEnd >= earliestStart) {
      return latestEnd - earliestStart;
    }

    return null;
  },

  estimateRemainingMs(stages, isLive, progressPct) {
    if (!isLive) return 0;
    const rows = Array.isArray(stages) ? stages : [];
    const elapsed = BELGIUM.pipelineDisplay.pipelineProcessingDurationMs(rows, true);
    const pct = Number(progressPct);
    const completedDurations = rows
      .map((stage) => {
        const start = stage?.started_at ? new Date(stage.started_at).getTime() : NaN;
        const end = stage?.completed_at ? new Date(stage.completed_at).getTime() : NaN;
        return Number.isFinite(start) && Number.isFinite(end) && end >= start ? end - start : null;
      })
      .filter((duration) => duration != null);

    if (completedDurations.length) {
      const average = completedDurations.reduce((sum, duration) => sum + duration, 0) / completedDurations.length;
      const pendingCount = rows.filter((stage) => {
        const status = String(stage?.status || "PENDING").toUpperCase();
        return status === "PENDING";
      }).length;
      const running = rows.find((stage) => String(stage?.status || "").toUpperCase() === "RUNNING");
      let runningRemaining = 0;
      if (running) {
        const stageStart = running.started_at ? new Date(running.started_at).getTime() : Date.now();
        const stageElapsed = Math.max(0, Date.now() - stageStart);
        const stagePct = Number(running.progress);
        runningRemaining = stagePct > 0 && stagePct < 100
          ? Math.max(0, stageElapsed * ((100 - stagePct) / stagePct))
          : Math.max(0, average - stageElapsed);
      }
      return Math.max(0, runningRemaining + pendingCount * average);
    }

    if (elapsed != null && pct > 1 && pct < 100) {
      return Math.max(0, elapsed * ((100 - pct) / pct));
    }
    return null;
  },

  formatBytes(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value < 0) return "—";
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
    return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  },

  mapStageStatus(status, isCurrent) {
    const normalized = String(status || "PENDING").toUpperCase();
    if (normalized === "COMPLETED") return "completed";
    if (
      normalized === "RUNNING" ||
      normalized === "PROCESSING" ||
      (isCurrent && normalized === "PENDING")
    ) {
      return "running";
    }
    if (normalized === "FAILED") return "failed";
    if (normalized === "SKIPPED") return "skipped";
    return "pending";
  },

  stageDurationText(stage, isLive) {
    if (!stage) return "";
    const started = stage.started_at ? new Date(stage.started_at).getTime() : null;
    const completed = stage.completed_at ? new Date(stage.completed_at).getTime() : null;
    if (started && completed) {
      return BELGIUM.pipelineDisplay.formatDurationMs(completed - started);
    }
    if (started && isLive) {
      return BELGIUM.pipelineDisplay.formatDurationMs(Date.now() - started);
    }
    return "";
  },

  pipelineTotalTimeText(job, progress) {
    if (!job && !progress) return "";
    const status = String(job?.status || progress?.status || "").toUpperCase();
    const isLive = status === "PROCESSING" || status === "QUEUED";
    const stages = BELGIUM.pipelineDisplay.resolveStagesForTiming(job, progress);
    const durationMs = BELGIUM.pipelineDisplay.pipelineProcessingDurationMs(stages, isLive);
    if (durationMs != null) {
      return BELGIUM.pipelineDisplay.formatProcessingTime(durationMs);
    }
    return "";
  },

  enrichAgentsWithFlow(stages, flowItems, agents, currentAgent, jobStatus) {
    const stageMap = {};
    (stages || []).forEach((stage) => {
      if (stage && stage.name) stageMap[stage.name] = stage;
    });

    const flow = Array.isArray(flowItems)
      ? flowItems.filter((item) => !HIDDEN_UI_AGENT_KEYS.has(item?.key))
      : [];
    const registry = Array.isArray(agents)
      ? agents.filter((agent) => !HIDDEN_UI_AGENT_KEYS.has(agent?.key))
      : [];
    const isLive = jobStatus === "PROCESSING" || jobStatus === "QUEUED";

    let items = flow;
    if (!items.length && registry.length) {
      items = registry.map((agent) => ({
        key: agent.key,
        enabled: agent.enabled_by_default !== false,
        order: agent.default_order,
      }));
    }

    const sorted = items.slice().sort((a, b) => Number(a.order || 0) - Number(b.order || 0));

    return sorted.map((item, index) => {
      const stage = stageMap[item.key];
      const agent = registry.find((row) => row.key === item.key);
      const inFlow = item.enabled !== false;
      const isCurrent = inFlow && currentAgent === item.key;
      const effectiveStatus = inFlow
        ? BELGIUM.pipelineDisplay.mapStageStatus(stage?.status, isCurrent && isLive)
        : "off";

      return {
        key: item.key,
        displayName: agent?.display_name || stage?.display_name || item.key,
        order: item.order || index + 1,
        selectedInFlow: inFlow,
        stage,
        effectiveStatus,
        isCurrent: isCurrent && effectiveStatus === "running",
        progress: Number(stage?.progress) || 0,
        recordProgress: BELGIUM.pipelineDisplay.stageRecordProgress(stage),
        durationText: inFlow ? BELGIUM.pipelineDisplay.stageDurationText(stage, isLive) : "",
        locked: Boolean(item.locked),
        required: Boolean(item.required),
      };
    });
  },

  stageRecordProgress(stage) {
    const result = stage?.result || {};
    const total = Number(result.total ?? result.record_count ?? result.processable_inputs ?? result.total_inputs);
    const done = Number(result.done ?? result.processed);
    const pending = Number(result.pending);
    if (Number.isFinite(total) && total > 0 && Number.isFinite(done)) {
      return {
        total,
        done,
        pending: Number.isFinite(pending) ? pending : Math.max(total - done, 0),
      };
    }
    if (Number.isFinite(total) && total > 0) {
      return { total, done: null, pending: null };
    }
    return null;
  },

  computePipelineProgress(enrichedAgents, job) {
    const inFlow = (enrichedAgents || []).filter((agent) => agent.selectedInFlow);
    const completedCount = inFlow.filter((agent) => agent.effectiveStatus === "completed").length;
    const selectedCount = inFlow.length;
    const offCount = (enrichedAgents || []).filter((agent) => !agent.selectedInFlow).length;
    const skippedCount = inFlow.filter((agent) => agent.effectiveStatus === "skipped").length;
    const runningAgent = inFlow.find(
      (agent) => agent.isCurrent || agent.effectiveStatus === "running"
    );

    let pct = 0;
    if (job?.progress_pct != null && Number(job.progress_pct) > 0) {
      pct = Math.round(Number(job.progress_pct));
    } else if (selectedCount) {
      pct = Math.round((completedCount / selectedCount) * 100);
    }
    if (String(job?.status || "").toUpperCase() === "COMPLETED") {
      pct = 100;
    }

    const segments = inFlow.map((agent) => ({
      key: agent.key,
      name: agent.displayName,
      segmentStatus:
        agent.effectiveStatus === "skipped" ? "pending" : agent.effectiveStatus || "pending",
      isCurrent: agent.isCurrent,
      progress: agent.progress,
    }));

    return {
      completedCount,
      selectedCount,
      offCount,
      skippedCount,
      pct,
      runningAgent,
      segments,
    };
  },

  flowStatusTag(status, inFlow) {
    if (!inFlow) return { label: "Off", className: "pipeline-tag-off" };
    const normalized = String(status || "").toLowerCase();
    if (normalized === "completed") return { label: "Done", className: "pipeline-tag-on" };
    if (normalized === "running") return { label: "Live", className: "pipeline-tag-live" };
    if (normalized === "failed") return { label: "Failed", className: "pipeline-tag-fail" };
    if (normalized === "skipped") return { label: "Skipped", className: "pipeline-tag-off" };
    return { label: "Pending", className: "pipeline-tag-on" };
  },
};
