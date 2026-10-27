window.BELGIUM = window.BELGIUM || {};

BELGIUM.BelgiumUploadModal = function BelgiumUploadModal({ onClose, onUploaded }) {
  const [error, setError] = React.useState("");
  const [warning, setWarning] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [files, setFiles] = React.useState([]);
  const [projectName, setProjectName] = React.useState("");
  const [projectNameTouched, setProjectNameTouched] = React.useState(false);
  const modelMode = "rf_model";
  const [dragOver, setDragOver] = React.useState(false);
  const inputRef = React.useRef(null);

  React.useEffect(() => {
    if (!files.length) {
      if (!projectNameTouched) setProjectName("");
      return;
    }
    if (!projectNameTouched) {
      setProjectName(BELGIUM.uploadMapping.projectNameFromFiles(files));
    }
  }, [files, projectNameTouched]);

  function addFiles(fileList) {
    const incoming = Array.from(fileList || []);
    if (!incoming.length) return;

    const accepted = [];
    const rejected = [];

    incoming.forEach((file) => {
      if (BELGIUM.uploadMapping.isSupportedFile(file)) {
        accepted.push(file);
      } else {
        rejected.push(file);
      }
    });

    if (rejected.length) {
      setError(
        `Unsupported file: ${rejected.map((file) => file.name).join(", ")}. Allowed files: KML (.kml, .kmz) and GeoPackage (.gpkg).`
      );
    } else {
      setError("");
    }

    if (!accepted.length) return;

    setFiles((prev) => {
      const merged = [...prev];
      accepted.forEach((file) => {
        const duplicate = merged.some(
          (existing) =>
            existing.name === file.name &&
            existing.size === file.size &&
            existing.lastModified === file.lastModified
        );
        if (!duplicate) merged.push(file);
      });
      return merged;
    });
  }

  function removeFile(index) {
    setFiles((prev) => prev.filter((_, fileIndex) => fileIndex !== index));
  }

  function handleDrop(event) {
    event.preventDefault();
    setDragOver(false);
    addFiles(event.dataTransfer.files);
  }

  async function submit() {
    setError("");
    setBusy(true);

    if (!files.length) {
      setError("Select a KML (.kml) or GeoPackage (.gpkg) file to upload.");
      setBusy(false);
      return;
    }

    const trimmedName = String(projectName || "").trim();
    if (!trimmedName) {
      setError("Project name is required.");
      setBusy(false);
      return;
    }

    const { assignments, skipped } = BELGIUM.uploadMapping.assignFilesToFields(files);
    if (!assignments.length) {
      setError("No supported KML or GeoPackage files could be mapped.");
      setBusy(false);
      return;
    }

    try {
      const inferredInputType = BELGIUM.uploadMapping.inferInputType(files);
      const formData = BELGIUM.uploadMapping.buildFormData({
        projectName: trimmedName,
        files,
        projectionMode: "auto",
        runMode: "minimal_belgium",
        inputType: inferredInputType,
        modelMode: modelMode,
        countyUrl: "",
        flowTemplateId: "",
        agentSequenceJson: "",
      });

      const result = await BELGIUM.belgiumApi.uploadJob(formData);
      onUploaded(result.job_id);
    } catch (err) {
      setError(err.message || "Project upload failed.");
    } finally {
      setBusy(false);
    }
  }

  const detectedSummary = files.length
    ? [...new Set(files.map((file) => BELGIUM.uploadMapping.detectType(file.name)))].join(", ")
    : "No files selected";

  return (
    <BELGIUM.Modal
      open
      title="Upload Belgium Project (KML / GPKG)"
      onClose={busy ? () => {} : onClose}
      size="lg"
      footer={
        <>
          <button
            type="button"
            className="app-button app-btn-secondary rounded-lg px-4 py-2"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className="app-button app-button-primary rounded-lg px-4 py-2 text-white"
            onClick={submit}
            disabled={!files.length || busy}
          >
            {busy ? (
              <span className="inline-flex items-center gap-2">
                <BELGIUM.Spinner size={14} /> Uploading…
              </span>
            ) : (
              `Upload ${files.length > 1 ? files.length + " files" : "file"}`
            )}
          </button>
        </>
      }
    >
      {/* Dropzone restricted to KML and GPKG */}
      <div
        className={`upload-dropzone ${dragOver ? "drag-over" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        aria-label="Drop KML or GPKG files here"
      >
        {files.length ? (
          <div className="space-y-1">
            <p className="font-medium">{files.length} file{files.length > 1 ? "s" : ""} selected</p>
            <p className="mx-auto max-w-md truncate text-xs" style={{ color: "var(--app-text-soft)" }}>
              Detected: {detectedSummary}
            </p>
          </div>
        ) : (
          <>
            <p className="mb-2 flex justify-center" style={{ color: "var(--app-text-soft)" }}>
              <BELGIUM.Icon name="upload" size={32} />
            </p>
            <p className="font-medium text-base">Drop KML (.kml) or GeoPackage (.gpkg) file here</p>
            <p className="mt-1 text-xs" style={{ color: "var(--app-text-soft)" }}>
              Only <strong>KML</strong> and <strong>GeoPackage (GPKG)</strong> spatial files are accepted
            </p>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".kml,.kmz,.gpkg"
          className="hidden"
          onChange={(event) => {
            addFiles(event.target.files);
            event.target.value = "";
          }}
        />
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="app-button app-btn-secondary rounded-lg px-3 py-2 text-sm"
        >
          Select KML / GPKG files
        </button>
        <button
          type="button"
          onClick={() => setFiles([])}
          className="app-button app-btn-secondary rounded-lg px-3 py-2 text-sm"
          disabled={!files.length || busy}
        >
          Clear selection
        </button>
      </div>

      <div className="mt-5 flex items-start gap-2 rounded-xl border border-cyan-500/30 bg-cyan-500/10 p-3 text-xs text-cyan-800 dark:text-cyan-200">
        <BELGIUM.Icon name="settings" size={15} />
        <span>Model, imagery, deduplication, and agent selections are configured from the project’s Flow Builder after upload.</span>
      </div>

      <div className="mt-4">
        <label className="mb-1 block text-sm font-medium" style={{ color: "var(--app-text)" }}>
          Project name
        </label>
        <input
          type="text"
          value={projectName}
          onChange={(event) => {
            setProjectNameTouched(true);
            setProjectName(event.target.value);
          }}
          className="app-input w-full rounded-lg px-3 py-2 text-sm"
          placeholder="e.g. Belgium Brussels Sector 1"
          required
        />
      </div>

      {files.length > 0 && (
        <div className="mt-4 space-y-2">
          <div className="text-sm font-medium" style={{ color: "var(--app-text)" }}>
            Selected KML / GPKG files
          </div>
          {files.map((file, index) => (
            <div key={`${file.name}-${file.size}-${file.lastModified}`} className="upload-file-chip">
              <div className="min-w-0">
                <div className="truncate font-medium">{file.name}</div>
                <div className="text-xs" style={{ color: "var(--app-text-soft)" }}>
                  {BELGIUM.uploadMapping.detectType(file.name)} · {BELGIUM.uploadMapping.formatFileSize(file.size)}
                </div>
              </div>
              <button
                type="button"
                className="app-button app-btn-secondary rounded px-2 py-1 text-xs"
                onClick={() => removeFile(index)}
                disabled={busy}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-lg border border-rose-300 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300">
          {error}
        </div>
      )}
    </BELGIUM.Modal>
  );
};
