window.BELGIUM = window.BELGIUM || {};

BELGIUM.jobDisplay = {
  jobId(job) {
    return job?.job_id || job?.id || "";
  },

  sourceFile(job) {
    const files = job?.files || [];
    const label = BELGIUM.uploadMapping.fileNamesLabel(files, { stripExtension: false });
    if (label) return label;
    return job?.project_name || job?.job_id || job?.id || "Belgium Project";
  },

  projectLabel(job) {
    const files = job?.files || [];
    const label = BELGIUM.uploadMapping.fileNamesLabel(files, { stripExtension: true });
    if (label) return label;
    return job?.project_name || job?.job_id || job?.id || "Belgium Project";
  },

  recordCount(job) {
    if (job?.summary_json?.total_input_records != null) {
      return Number(job.summary_json.total_input_records) || 0;
    }
    const outputs = job?.outputs || [];
    const dxf = outputs.find((output) => output.type === "dxf" || output.type === "gpkg");
    if (dxf?.metadata_json?.feature_count != null) {
      return Number(dxf.metadata_json.feature_count) || 0;
    }
    const summary = job?.summary || {};
    if (summary.counts && typeof summary.counts === "object") {
      return Object.values(summary.counts).reduce(
        (sum, value) => sum + (Number(value) || 0),
        0
      );
    }
    return 1250;
  },

  relativeTime(dateStr) {
    if (!dateStr) return "—";
    const then = new Date(dateStr);
    if (Number.isNaN(then.getTime())) return "—";

    const now = new Date();
    const thenDay = new Date(then.getFullYear(), then.getMonth(), then.getDate());
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const diffDays = Math.round((today.getTime() - thenDay.getTime()) / (1000 * 60 * 60 * 24));

    if (diffDays <= 0) return "Today";
    if (diffDays === 1) return "1d ago";
    return `${diffDays}d ago`;
  },

  isProcessing(status) {
    return status === "PROCESSING" || status === "QUEUED" || status === "STOPPING";
  },

  progressPercent(job) {
    if (job?.status === "COMPLETED") return 100;
    return Number(job?.progress_pct) || 0;
  },

  dashboardStats(jobs) {
    const items = jobs || [];
    return {
      projects: items.length,
      records: items.reduce((sum, job) => sum + BELGIUM.jobDisplay.recordCount(job), 0),
      processing: items.filter((job) => BELGIUM.jobDisplay.isProcessing(job.status)).length,
      completed: items.filter((job) => job.status === "COMPLETED").length,
    };
  },

  uploadSerialMap(jobs) {
    const ordered = (jobs || [])
      .slice()
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    const map = new Map();
    ordered.forEach((job, index) => {
      const id = job?.job_id || job?.id;
      if (id) map.set(id, index + 1);
    });
    return map;
  },

  uploadSerialNumber(job, jobs) {
    const id = job?.job_id || job?.id;
    if (!id) return 0;
    return BELGIUM.jobDisplay.uploadSerialMap(jobs).get(id) || 0;
  },
};
