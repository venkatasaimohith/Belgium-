window.BELGIUM = window.BELGIUM || {};

// Client-side local storage fallback state for pure frontend mode
(function initMockStore() {
  const STORAGE_KEY = "belgium_belgium_jobs_v6";
  if (!localStorage.getItem(STORAGE_KEY)) {
    const initialJobs = [
      {
        id: "job-belgium-dg040-000",
        job_id: "job-belgium-dg040-000",
        project_name: "Input_DG040-SSV-20251201.gpkg",
        input_type: "GPKG",
        model_mode: "rf_model",
        run_mode: "validation_pipeline",
        projection_mode: "auto",
        status: "UPLOADED",
        progress_pct: 0,
        current_agent: null,
        created_at: new Date(Date.now() - 3600000 * 2).toISOString(),
        updated_at: new Date(Date.now() - 1800000).toISOString(),
        files: [
          { id: "f0", file_type: "gpkg", original_filename: "Input_DG040-SSV-20251201.gpkg", size_bytes: 5545984 },
        ],
        outputs: [],
        summary_json: {
          total_input_records: 18547,
          unique_addresses: 12410,
          views_inspected: 4,
          model_selected: "RF-DETR Large Mailbox Bundle",
        },
      },
      {
        id: "job-belgium-home-001",
        job_id: "job-belgium-home-001",
        project_name: "Belgium_Home.gpkg",
        input_type: "GPKG",
        model_mode: "auto_confidence",
        run_mode: "validation_pipeline",
        projection_mode: "auto",
        status: "COMPLETED",
        progress_pct: 100,
        current_agent: "export_results",
        created_at: new Date(Date.now() - 3600000 * 24 * 2).toISOString(),
        updated_at: new Date(Date.now() - 3600000 * 2).toISOString(),
        files: [
          { id: "f1", file_type: "gpkg", original_filename: "Belgium_Home.gpkg", size_bytes: 1250000 },
        ],
        outputs: [
          { type: "kml", file_path: "outputs/Belgium_Home.kml", metadata_json: { feature_count: 1250 } },
          { type: "gpkg", file_path: "outputs/Belgium_Home.gpkg", metadata_json: { layer_count: 4 } },
        ],
        summary_json: {
          total_input_records: 1250,
          unique_addresses: 870,
          streetview_available: 840,
          no_streetview: 30,
          buildings_confirmed: 820,
          buildings_missing: 20,
          mailboxes_detected: 1420,
          new_units_added: 170,
          views_inspected: 4,
          model_selected: "RF-DETR Large Mailbox Bundle",
        },
      },
      {
        id: "job-belgium-antwerp-002",
        job_id: "job-belgium-antwerp-002",
        project_name: "Belgium_Antwerp_Sector2.gpkg",
        input_type: "GPKG",
        model_mode: "rf_model",
        run_mode: "validation_pipeline",
        projection_mode: "auto",
        status: "PROCESSING",
        progress_pct: 55,
        current_agent: "agent3_image",
        created_at: new Date(Date.now() - 3600000 * 5).toISOString(),
        updated_at: new Date(Date.now() - 60000 * 12).toISOString(),
        files: [
          { id: "f2", file_type: "gpkg", original_filename: "Belgium_Antwerp_Sector2.gpkg", size_bytes: 940000 },
        ],
        outputs: [],
        summary_json: { total_input_records: 620, unique_addresses: 410, streetview_available: 395 },
      },
      {
        id: "job-belgium-ghent-003",
        job_id: "job-belgium-ghent-003",
        project_name: "Belgium_Ghent_North.kml",
        input_type: "KML",
        model_mode: "rf_model",
        run_mode: "validation_pipeline",
        projection_mode: "auto",
        status: "UPLOADED",
        progress_pct: 0,
        current_agent: null,
        created_at: new Date(Date.now() - 3600000 * 1).toISOString(),
        updated_at: new Date(Date.now() - 3600000 * 1).toISOString(),
        files: [
          { id: "f3", file_type: "kml", original_filename: "Belgium_Ghent_North.kml", size_bytes: 512000 },
        ],
        outputs: [],
        summary_json: { total_input_records: 380, unique_addresses: 290 },
      },
    ];
    localStorage.setItem(STORAGE_KEY, JSON.stringify(initialJobs));
  }
})();

const STORAGE_KEY = "belgium_belgium_jobs_v6";

function getMockJobs() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return raw.map((job) => {
      const realId = job.id || job.job_id || `job-${Date.now()}`;
      return {
        ...job,
        id: realId,
        job_id: realId,
      };
    });
  } catch (_) {
    return [];
  }
}

function saveMockJobs(jobs) {
  const normalized = (jobs || []).map((j) => {
    const realId = j.id || j.job_id;
    return { ...j, id: realId, job_id: realId };
  });
  localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
}

BELGIUM.belgiumApi = {
  listJobs: async () => {
    try {
      const res = await BELGIUM.http("/api/belgium/jobs");
      if (res && res.jobs) {
        return {
          jobs: res.jobs.map((j) => ({ ...j, id: j.id || j.job_id, job_id: j.job_id || j.id })),
        };
      }
      return { jobs: getMockJobs() };
    } catch (_) {
      return { jobs: getMockJobs() };
    }
  },

  getJob: async (jobId) => {
    try {
      const res = await BELGIUM.http(`/api/belgium/jobs/${jobId}`);
      if (res) return { ...res, id: res.id || res.job_id || jobId, job_id: res.job_id || res.id || jobId };
      throw new Error("Not found");
    } catch (err) {
      const job = getMockJobs().find((j) => j.id === jobId || j.job_id === jobId);
      if (!job) throw err;
      return job;
    }
  },

  getJobRecords: async (jobId) => {
    try {
      const limit = 500;
      const first = await BELGIUM.http(`/api/belgium/jobs/${jobId}/records?page=1&limit=${limit}`);
      const records = [...first.records];
      for (let page = 2; page <= first.total_pages; page++) {
        const next = await BELGIUM.http(`/api/belgium/jobs/${jobId}/records?page=${page}&limit=${limit}`);
        records.push(...next.records);
      }
      return { ...first, records };
    } catch (err) {
      const job = getMockJobs().find((j) => j.id === jobId || j.job_id === jobId);
      if (!job) throw err;
      const isJobNotRun = !job || job.status === "UPLOADED" || job.status === "PENDING";

      if (job && Array.isArray(job.parsed_records) && job.parsed_records.length > 0) {
        let records = job.parsed_records;
        if (isJobNotRun) {
          records = records.map((r) => ({
            ...r,
            street_view_status: "PENDING",
            building_exists: "PENDING",
            mailboxes_detected: null,
            added_records: null,
            status: "PENDING",
          }));
        }
        return {
          count: job.summary_json?.total_input_records || records.length,
          records,
        };
      }

      const filename = String(job?.project_name || "").toLowerCase();
      const isKml = filename.endsWith(".kml") || filename.endsWith(".kmz");
      const ext = isKml ? "KML" : "GPKG";

      if (filename.includes("dg040") || filename.includes("ssv")) {
        const baseRecords = [
          { id: `rec-${jobId}-01`, address: "Liv. Walravenslaan 15 box 202, 1700 Dilbeek", original_row_count: 2, unique_address_id: "ADDR-DLB-4913189", street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "YES", mailboxes_detected: isJobNotRun ? null : 4, added_records: isJobNotRun ? null : 2, status: isJobNotRun ? "PENDING" : "COMPLETED" },
          { id: `rec-${jobId}-02`, address: "Ninoofsesteenweg 839 box 1, 1703 Dilbeek", original_row_count: 1, unique_address_id: "ADDR-DLB-4422077", street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "YES", mailboxes_detected: isJobNotRun ? null : 1, added_records: isJobNotRun ? null : 0, status: isJobNotRun ? "PENDING" : "COMPLETED" },
          { id: `rec-${jobId}-03`, address: "Lentestraat 1, 1702 Dilbeek", original_row_count: 1, unique_address_id: "ADDR-DLB-3481394", street_view_status: isJobNotRun ? "PENDING" : "NO", building_exists: isJobNotRun ? "PENDING" : "UNCHECKED", mailboxes_detected: isJobNotRun ? null : 0, added_records: isJobNotRun ? null : 0, status: isJobNotRun ? "PENDING" : "To Be Checked" },
          { id: `rec-${jobId}-04`, address: "Bodegemstraat 45, 1700 Dilbeek", original_row_count: 1, unique_address_id: "ADDR-DLB-5102941", street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "YES", mailboxes_detected: isJobNotRun ? null : 2, added_records: isJobNotRun ? null : 1, status: isJobNotRun ? "PENDING" : "COMPLETED" },
          { id: `rec-${jobId}-05`, address: "Itterbeeksebaan 120, 1701 Dilbeek", original_row_count: 3, unique_address_id: "ADDR-DLB-3928104", street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "YES", mailboxes_detected: isJobNotRun ? null : 5, added_records: isJobNotRun ? null : 2, status: isJobNotRun ? "PENDING" : "COMPLETED" },
          { id: `rec-${jobId}-06`, address: "Verheydenstraat 28, 1700 Dilbeek", original_row_count: 1, unique_address_id: "ADDR-DLB-4192048", street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "NO", mailboxes_detected: isJobNotRun ? null : 0, added_records: isJobNotRun ? null : 0, status: isJobNotRun ? "PENDING" : "To Be Checked" },
          { id: `rec-${jobId}-07`, address: "Spanjebergstraat 14, 1700 Dilbeek", original_row_count: 1, unique_address_id: "ADDR-DLB-4820194", street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "YES", mailboxes_detected: isJobNotRun ? null : 1, added_records: isJobNotRun ? null : 0, status: isJobNotRun ? "PENDING" : "COMPLETED" },
          { id: `rec-${jobId}-08`, address: "d'Arconatistraat 8, 1700 Dilbeek", original_row_count: 2, unique_address_id: "ADDR-DLB-4610283", street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "YES", mailboxes_detected: isJobNotRun ? null : 3, added_records: isJobNotRun ? null : 1, status: isJobNotRun ? "PENDING" : "COMPLETED" },
        ];
        return {
          count: job?.summary_json?.total_input_records || 18547,
          records: baseRecords,
        };
      }

      return {
        count: job?.summary_json?.total_input_records || 1250,
        records: [
          { id: `rec-${jobId}-01`, address: "Rue Royale 10, 1000 Bruxelles", original_row_count: 2, unique_address_id: `ADDR-BRU-${ext}-01`, street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "YES", mailboxes_detected: isJobNotRun ? null : 4, added_records: isJobNotRun ? null : 2, status: isJobNotRun ? "PENDING" : "COMPLETED" },
          { id: `rec-${jobId}-02`, address: "Meir 42, 2000 Antwerpen", original_row_count: 1, unique_address_id: `ADDR-ANT-${ext}-02`, street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "YES", mailboxes_detected: isJobNotRun ? null : 1, added_records: isJobNotRun ? null : 0, status: isJobNotRun ? "PENDING" : "COMPLETED" },
          { id: `rec-${jobId}-03`, address: "Veldstraat 15, 9000 Gent", original_row_count: 1, unique_address_id: `ADDR-GNT-${ext}-03`, street_view_status: isJobNotRun ? "PENDING" : "NO", building_exists: isJobNotRun ? "PENDING" : "UNCHECKED", mailboxes_detected: isJobNotRun ? null : 0, added_records: isJobNotRun ? null : 0, status: isJobNotRun ? "PENDING" : "To Be Checked" },
          { id: `rec-${jobId}-04`, address: "Stationstraat 88, 8000 Brugge", original_row_count: 1, unique_address_id: `ADDR-BRG-${ext}-04`, street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "NO", mailboxes_detected: isJobNotRun ? null : 0, added_records: isJobNotRun ? null : 0, status: isJobNotRun ? "PENDING" : "To Be Checked" },
          { id: `rec-${jobId}-05`, address: "Bondgenotenlaan 104, 3000 Leuven", original_row_count: 3, unique_address_id: `ADDR-LVN-${ext}-05`, street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "YES", mailboxes_detected: isJobNotRun ? null : 5, added_records: isJobNotRun ? null : 2, status: isJobNotRun ? "PENDING" : "COMPLETED" },
          { id: `rec-${jobId}-06`, address: "Rue de Fer 24, 5000 Namur", original_row_count: 1, unique_address_id: `ADDR-NMR-${ext}-06`, street_view_status: isJobNotRun ? "PENDING" : "YES", building_exists: isJobNotRun ? "PENDING" : "YES", mailboxes_detected: isJobNotRun ? null : 2, added_records: isJobNotRun ? null : 1, status: isJobNotRun ? "PENDING" : "COMPLETED" },
        ],
      };
    }
  },

  uploadJob: async (formData) => {
    try {
      const res = await BELGIUM.http("/api/belgium/upload", { method: "POST", body: formData });
      if (res && res.job_id) return res;
      throw new Error("Fallback to mock store");
    } catch (_) {
      const projectName = formData.get("project_name") || "Belgium_Spatial.gpkg";
      const inputType = formData.get("input_type") || (String(projectName).toLowerCase().endsWith(".kml") || String(projectName).toLowerCase().endsWith(".kmz") ? "KML" : "GPKG");
      const modelMode = formData.get("model_mode") || "rf_model";
      const ext = inputType === "KML" ? "kml" : "gpkg";
      const newId = `job-belgium-${Date.now()}`;

      const fileObj = formData.get("gpkg_file") || formData.get("kml_file") || formData.get("kmz_file");
      let meta = { recordCount: 1250, uniqueAddresses: 870, parsedRecords: [] };
      if (fileObj && typeof BELGIUM.uploadMapping?.parseFileMetadata === "function") {
        try {
          meta = await BELGIUM.uploadMapping.parseFileMetadata(fileObj);
        } catch (_) {}
      }

      const newJob = {
        id: newId,
        job_id: newId,
        project_name: projectName,
        input_type: inputType,
        model_mode: modelMode,
        run_mode: "validation_pipeline",
        projection_mode: "auto",
        status: "UPLOADED",
        progress_pct: 0,
        current_agent: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        files: [{ id: `f-${Date.now()}`, file_type: ext, original_filename: `${projectName}`, size_bytes: fileObj ? fileObj.size : 1250000 }],
        outputs: [],
        summary_json: { total_input_records: meta.recordCount, unique_addresses: meta.uniqueAddresses },
        parsed_records: meta.parsedRecords,
      };
      const jobs = getMockJobs();
      jobs.unshift(newJob);
      saveMockJobs(jobs);
      return { job_id: newId, id: newId, status: newJob.status, message: "Belgium validation project uploaded successfully", files: newJob.files };
    }
  },

  processJob: async (jobId) => {
    try {
      return await BELGIUM.http(`/api/belgium/jobs/${jobId}/process`, { method: "POST" });
    } catch (_) {
      const jobs = getMockJobs();
      const job = jobs.find((j) => j.id === jobId);
      if (job) {
        job.status = "PROCESSING";
        job.progress_pct = 11;
        job.current_agent = "agent1_dedup";
        job.updated_at = new Date().toISOString();
        saveMockJobs(jobs);

        // Agents 1–7 mailbox and floor-count pipeline (+ export)
        const stagesList = [
          "agent1_dedup",
          "agent2_parcel",
          "agent3_image",
          "agent4_assign",
          "agent5_detect",
          "agent6_reject",
          "agent7_floor_count",
          "export_results",
        ];

        let stepIndex = 0;
        const interval = setInterval(() => {
          stepIndex += 1;
          const currentJobs = getMockJobs();
          const target = currentJobs.find((j) => j.id === jobId);
          if (!target || target.status !== "PROCESSING") {
            clearInterval(interval);
            return;
          }
          if (stepIndex < stagesList.length) {
            target.current_agent = stagesList[stepIndex];
            target.progress_pct = Math.round(((stepIndex + 1) / stagesList.length) * 100);
            saveMockJobs(currentJobs);
          } else {
            target.status = "COMPLETED";
            target.progress_pct = 100;
            target.current_agent = "export_results";
            if (Array.isArray(target.parsed_records)) {
              target.parsed_records = target.parsed_records.map((r, idx) => ({
                ...r,
                street_view_status: idx % 5 === 3 ? "NO" : "YES",
                building_exists: idx % 5 === 3 ? "UNCHECKED" : idx % 7 === 6 ? "NO" : "YES",
                mailboxes_detected: idx % 5 === 3 ? 0 : (idx % 4) + 1,
                added_records: idx % 4 === 2 ? 1 : 0,
                status: idx % 5 === 3 || idx % 7 === 6 ? "To Be Checked" : "COMPLETED",
              }));
            }
            target.outputs = [
              { type: "kml", file_path: `outputs/${jobId}.kml`, metadata_json: { feature_count: 1250 } },
              { type: "gpkg", file_path: `outputs/${jobId}.gpkg`, metadata_json: { layer_count: 4 } },
            ];
            saveMockJobs(currentJobs);
            clearInterval(interval);
          }
        }, 1200);
      }
      return {
        job_id: jobId,
        status: "QUEUED",
        stages: [
          "agent1_dedup",
          "agent2_parcel",
          "agent3_image",
          "agent4_assign",
          "agent5_detect",
          "agent6_reject",
          "agent7_floor_count",
          "export_results",
        ],
      };
    }
  },

  resumeJob: async (jobId) => BELGIUM.belgiumApi.processJob(jobId),

  stopJob: async (jobId) => {
    try {
      return await BELGIUM.http(`/api/belgium/jobs/${jobId}/stop`, { method: "POST" });
    } catch (_) {
      const jobs = getMockJobs();
      const job = jobs.find((j) => j.id === jobId);
      if (job) {
        job.status = "FAILED";
        job.updated_at = new Date().toISOString();
        saveMockJobs(jobs);
      }
      return { job_id: jobId, status: "FAILED", message: "Force stopped by user" };
    }
  },

  getProgress: async (jobId) => {
    try {
      return await BELGIUM.http(`/api/belgium/jobs/${jobId}/progress`);
    } catch (_) {
      const job = getMockJobs().find((j) => j.id === jobId);
      return {
        job_id: jobId,
        status: job ? job.status : "COMPLETED",
        progress_pct: job ? job.progress_pct : 100,
        current_agent: job ? job.current_agent : null,
      };
    }
  },

  getEvents: async (jobId) => {
    try {
      return await BELGIUM.http(`/api/belgium/jobs/${jobId}/events`);
    } catch (_) {
      return {
        events: [
          { id: 1, event_type: "agent", level: "INFO", message: "Agent 1: Input + Dedup", timestamp: new Date().toISOString() },
          { id: 2, event_type: "agent", level: "INFO", message: "Agent 2: Parcel (CadGIS)", timestamp: new Date().toISOString() },
          { id: 3, event_type: "agent", level: "INFO", message: "Agent 3: Street View images", timestamp: new Date().toISOString() },
          { id: 4, event_type: "agent", level: "INFO", message: "Agent 4: Image enhancement", timestamp: new Date().toISOString() },
          { id: 5, event_type: "agent", level: "INFO", message: "Agent 5: Detect all mailboxes", timestamp: new Date().toISOString() },
          { id: 6, event_type: "agent", level: "INFO", message: "Agent 6: Reject neighbour mailboxes", timestamp: new Date().toISOString() },
          { id: 7, event_type: "agent", level: "INFO", message: "Agent 7: Count building floors", timestamp: new Date().toISOString() },
        ],
      };
    }
  },

  listAgents: async () => {
    try {
      return await BELGIUM.http("/api/belgium/agents");
    } catch (_) {
      return {
        agents: [
          { key: "agent1_dedup", display_name: "1. Input + Dedup", description: "Normalise addresses and group multi-unit dwellings" },
          { key: "agent2_parcel", display_name: "2. Parcel", description: "Download the target CadGIS parcel information" },
          { key: "agent3_image", display_name: "3. Street View Images", description: "Best panorama at FOV 90 and 110" },
          { key: "agent4_assign", display_name: "4. Image Enhancement", description: "Real-ESRGAN 2× enhancement" },
          { key: "agent5_detect", display_name: "5. Detect Mailboxes (A + B)", description: "Agent 5A RF-DETR with Agent 5B local Florence-2 verification" },
          { key: "agent6_reject", display_name: "6. Reject Neighbour Mailboxes", description: "Target-house, facade and driveway ownership filtering" },
          { key: "agent7_floor_count", display_name: "7. Floor Count", description: "Target-facade window-row floor estimation" },
          { key: "export_results", display_name: "Export Results", description: "Build records table and GPKG/KML outputs" },
        ],
      };
    }
  },

  getDefaultFlow: async () => {
    try {
      return await BELGIUM.http("/api/belgium/flows/default");
    } catch (_) {
      return {
        flow: [
          { key: "agent1_dedup", enabled: true, order: 1 },
          { key: "agent2_parcel", enabled: true, order: 2 },
          { key: "agent3_image", enabled: true, order: 3 },
          { key: "agent4_assign", enabled: true, order: 4 },
          { key: "agent5_detect", enabled: true, order: 5 },
          { key: "agent6_reject", enabled: true, order: 6 },
          { key: "agent7_floor_count", enabled: true, order: 7 },
          { key: "export_results", enabled: true, order: 8 },
        ],
      };
    }
  },

  listFlowTemplates: async () => {
    try {
      return await BELGIUM.http("/api/belgium/flows/templates");
    } catch (_) {
      return { templates: [] };
    }
  },

  createFlowTemplate: async (payload) => ({ id: `tpl-${Date.now()}`, ...payload }),
  updateFlowTemplate: async (id, payload) => ({ id, ...payload }),
  deleteFlowTemplate: async (id) => ({ status: "deleted", template_id: id }),
  validateFlow: async (flow) => ({ valid: true, errors: [], warnings: [] }),
  getJobFlow: async (jobId) => {
    try {
      return await BELGIUM.http(`/api/belgium/jobs/${jobId}/flow`);
    } catch (_) {
      return BELGIUM.belgiumApi.getDefaultFlow();
    }
  },
  updateJobFlow: async (jobId, payload) => BELGIUM.http(`/api/belgium/jobs/${jobId}/flow`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  }),
  updateJobOptions: async (jobId, payload) => BELGIUM.http(`/api/belgium/jobs/${jobId}/options`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  }),

  deleteJob: async (jobId) => {
    try {
      return await BELGIUM.http(`/api/belgium/jobs/${jobId}`, { method: "DELETE" });
    } catch (_) {
      const jobs = getMockJobs().filter((j) => j.id !== jobId);
      saveMockJobs(jobs);
      return { status: "deleted", job_id: jobId };
    }
  },

  bulkDeleteJobs: async (jobIds) => {
    try {
      return await BELGIUM.http("/api/belgium/jobs/bulk-delete", {
        method: "POST",
        body: JSON.stringify({ job_ids: jobIds }),
        headers: { "Content-Type": "application/json" },
      });
    } catch (_) {
      const idSet = new Set(jobIds);
      const jobs = getMockJobs().filter((j) => !idSet.has(j.id));
      saveMockJobs(jobs);
      return { status: "deleted", deleted_job_ids: jobIds, errors: [] };
    }
  },
};

BELGIUM.belgiumApi.canDelete = function canDelete(status) {
  return status !== "PROCESSING" && status !== "QUEUED" && status !== "STOPPING";
};

BELGIUM.belgiumApi.downloadOutput = async function downloadOutput(jobId, outputType, streetviewYear = "latest") {
  if (outputType === "both") {
    await BELGIUM.belgiumApi.downloadOutput(jobId, "kml");
    await new Promise((resolve) => setTimeout(resolve, 500));
    await BELGIUM.belgiumApi.downloadOutput(jobId, "gpkg");
    return;
  }

  if (outputType === "streetview") {
    const response = await fetch(
      `/api/belgium/jobs/${jobId}/streetview-images?year=${encodeURIComponent(streetviewYear || "latest")}`
    );
    if (!response.ok) {
      let message = "Street View images are unavailable for the selected year.";
      try {
        const body = await response.json();
        message = body?.detail || message;
      } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition");
    let filename = `streetview_${jobId}_${streetviewYear || "latest"}.zip`;
    if (disposition && disposition.includes("filename=")) {
      filename = disposition.split("filename=")[1].replace(/"/g, "").trim();
    }
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
    return;
  }

  // Try server endpoint if backend API server is available
  try {
    const response = await fetch(`/api/belgium/jobs/${jobId}/export?format=${outputType}`);
    if (response.ok) {
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition");
      let filename = `belgium_output_${jobId}.${outputType}`;
      if (disposition && disposition.includes("filename=")) {
        filename = disposition.split("filename=")[1].replace(/"/g, "").trim();
      }
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      return;
    }
  } catch (_) {
    // Fall back to rich client-side generator
  }

  const job = await BELGIUM.belgiumApi.getJob(jobId).catch(() => null);
  const recordsData = await BELGIUM.belgiumApi.getJobRecords(jobId).catch(() => ({ records: [] }));
  const records = (recordsData && recordsData.records && recordsData.records.length > 0)
    ? recordsData.records
    : (job?.records || []);

  const baseName = job?.project_name ? job.project_name.replace(/\.[^/.]+$/, "") : `belgium_${jobId}`;
  let content = "";
  let mimeType = "text/plain";
  let extension = outputType;

  if (outputType === "kml") {
    extension = "kml";
    mimeType = "application/vnd.google-earth.kml+xml";

    const placemarks = records.map((r, i) => {
      const lat = r.lat || (50.8467 + (i * 0.0012)).toFixed(6);
      const lng = r.lng || (4.3524 + (i * 0.0015)).toFixed(6);
      const mbCount = r.mailboxes_detected !== null && r.mailboxes_detected !== undefined ? r.mailboxes_detected : "N/A";
      const statusStr = r.status || "PENDING";
      const addressName = (r.address || `Address #${i+1}`).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      
      return `    <Placemark>
      <name>${addressName}</name>
      <ExtendedData>
        <Data name="unique_address_id"><value>${r.unique_address_id || ""}</value></Data>
        <Data name="status"><value>${statusStr}</value></Data>
        <Data name="mailboxes_detected"><value>${mbCount}</value></Data>
        <Data name="building_exists"><value>${r.building_exists || "PENDING"}</value></Data>
        <Data name="street_view_status"><value>${r.street_view_status || "PENDING"}</value></Data>
      </ExtendedData>
      <Point><coordinates>${lng},${lat},0</coordinates></Point>
    </Placemark>`;
    }).join("\n");

    content = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>${baseName}_Output</name>
    <description>Belgium Address Validation &amp; Mailbox Pipeline Results for ${jobId}</description>
${placemarks || `    <Placemark><name>Default Brussels Landmark</name><Point><coordinates>4.3524,50.8467,0</coordinates></Point></Placemark>`}
  </Document>
</kml>`;
  } else if (outputType === "gpkg") {
    extension = "gpkg";
    mimeType = "application/geopackage+sqlite3";

    const recordLines = records.map((r, i) => 
      `Record ${i+1}: ID=${r.unique_address_id || r.id}, Address="${r.address || ""}", Status=${r.status || "PENDING"}, Mailboxes=${r.mailboxes_detected ?? "N/A"}, BuildingExists=${r.building_exists || "PENDING"}`
    ).join("\n");

    content = `SQLite format 3\x00 GPKG Header\nBelgium GeoPackage Spatial Database Output\nProject ID: ${jobId}\nProject File: ${job?.project_name || "Belgium_Spatial.gpkg"}\nTotal Records: ${records.length}\n\n[LAYER: belgium_validated_addresses]\n${recordLines}`;
  } else {
    extension = outputType;
    mimeType = "text/plain";
    content = `Belgium Project Output (${outputType})\nProject ID: ${jobId}`;
  }

  const blob = new Blob([content], { type: mimeType });
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${baseName}_${outputType}.${extension}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};

BELGIUM.belgiumApi.processButtonLabel = function processButtonLabel(status) {
  if (status === "FAILED") return "Retry";
  if (status === "STOPPING") return "Stopping...";
  if (status === "QUEUED" || status === "PROCESSING") return "Processing...";
  if (status === "COMPLETED") return "Completed";
  return "Start Validation Pipeline";
};

BELGIUM.belgiumApi.canProcess = function canProcess(status) {
  return status === "UPLOADED" || status === "FAILED" || status === "COMPLETED";
};

BELGIUM.belgiumApi.canStop = function canStop(status) {
  return status === "PROCESSING" || status === "QUEUED";
};

BELGIUM.belgiumApi.isProcessingStatus = function isProcessingStatus(status) {
  return status === "PROCESSING" || status === "QUEUED" || status === "STOPPING";
};

BELGIUM.belgiumApi.processActionLabel = function processActionLabel(status) {
  if (BELGIUM.belgiumApi.isProcessingStatus(status)) return "Processing…";
  if (status === "FAILED" || status === "COMPLETED") return "Re-Run Validation Pipeline";
  return "Start Validation Pipeline";
};

BELGIUM.belgiumApi.hasOutput = function hasOutput(job, outputType) {
  return Boolean((job?.outputs || []).some((output) => output.type === outputType)) || job?.status === "COMPLETED";
};

BELGIUM.belgiumApi.canDownloadOutput = function canDownloadOutput(job, outputType) {
  if (!job) return false;
  if (outputType === "kml" || outputType === "gpkg" || outputType === "both") {
    return !BELGIUM.belgiumApi.isProcessingStatus(job.status);
  }
  return true;
};

BELGIUM.belgiumApi.agentLabel = function agentLabel(key, agents) {
  const agent = (agents || []).find((item) => item.key === key);
  return agent ? agent.display_name : key;
};

BELGIUM.belgiumApi.agentDescription = function agentDescription(key, agents) {
  const agent = (agents || []).find((item) => item.key === key);
  return agent ? agent.description : "";
};
