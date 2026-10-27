window.BELGIUM = window.BELGIUM || {};

BELGIUM.ProjectDetailActions = function ProjectDetailActions({
  job,
  onBack,
  onOpenFlow,
  onProcess,
  onStop,
  processBusy,
  stopBusy,
  onDownload,
  downloadBusy,
}) {
  const status = job?.status || "";
  const isProcessing = BELGIUM.belgiumApi.isProcessingStatus(status);
  const canProcess = BELGIUM.belgiumApi.canProcess(status);
  const canStop = BELGIUM.belgiumApi.canStop(status);
  const canDownloadKml = BELGIUM.belgiumApi.canDownloadOutput(job, "kml");
  const canDownloadGpkg = BELGIUM.belgiumApi.canDownloadOutput(job, "gpkg");
  const canDownloadStreetview = status === "COMPLETED";
  const streetviewYear = job?.streetview_year || job?.options?.streetview_year || "latest";
  const streetviewYearLabel = String(streetviewYear).toLowerCase() === "latest" ? "Latest" : streetviewYear;

  return (
    <div className="action-bar flex flex-wrap items-center gap-2">
      <button type="button" className="app-button app-btn-secondary action-bar-btn" onClick={onBack}>
        <BELGIUM.Icon name="back" size={14} />
        Back
      </button>

      <button
        type="button"
        className="app-button action-bar-btn action-bar-flow"
        onClick={onOpenFlow}
        title="Configure the agent flow, model, imagery, and processing options"
      >
        <BELGIUM.Icon name="settings" size={14} />
        Flow Builder
      </button>

      {canProcess && !isProcessing && (
        <button
          type="button"
          className={`app-button action-bar-btn ${
            status === "COMPLETED" || status === "FAILED" ? "action-bar-reprocess" : "action-bar-process"
          }`}
          disabled={processBusy}
          onClick={onProcess}
        >
          <BELGIUM.Icon name={status === "FAILED" || status === "COMPLETED" ? "refresh" : "play"} size={14} />
          {BELGIUM.belgiumApi.processActionLabel(status)}
        </button>
      )}

      {canStop && (
        <button
          type="button"
          className="app-button action-bar-btn action-bar-force-stop"
          disabled={stopBusy}
          onClick={onStop}
          title="Stop the running pipeline"
        >
          {stopBusy ? <BELGIUM.Spinner size={14} /> : <BELGIUM.Icon name="x" size={14} />}
          Force stop
        </button>
      )}

      <div className="ml-auto flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="app-button action-bar-btn action-bar-download border border-cyan-500/30 bg-cyan-600/20 text-cyan-700 hover:bg-cyan-600/30 dark:text-cyan-300"
          disabled={!canDownloadStreetview || downloadBusy}
          onClick={() => onDownload("streetview")}
          title={canDownloadStreetview ? `Download ${streetviewYearLabel} Street View images as ZIP` : "Run the pipeline before downloading Street View images"}
        >
          <BELGIUM.Icon name="download" size={14} />
          Street View ({streetviewYearLabel})
        </button>

        <button
          type="button"
          className="app-button action-bar-btn action-bar-download"
          disabled={!canDownloadKml || downloadBusy}
          onClick={() => onDownload("kml")}
          title={canDownloadKml ? "Download KML output file" : "KML output unavailable"}
        >
          <BELGIUM.Icon name="download" size={14} />
          Download KML
        </button>

        <button
          type="button"
          className="app-button action-bar-btn action-bar-download"
          disabled={!canDownloadGpkg || downloadBusy}
          onClick={() => onDownload("gpkg")}
          title={canDownloadGpkg ? "Download GeoPackage (GPKG) database output" : "GeoPackage output unavailable"}
        >
          <BELGIUM.Icon name="download" size={14} />
          Download GPKG
        </button>

        <button
          type="button"
          className="app-button action-bar-btn action-bar-download border border-blue-500/30 bg-blue-600/20 text-blue-300 hover:bg-blue-600/30 font-semibold"
          disabled={(!canDownloadKml && !canDownloadGpkg) || downloadBusy}
          onClick={() => onDownload("both")}
          title="Download both KML and GeoPackage (GPKG) spatial files"
        >
          <BELGIUM.Icon name="download" size={14} />
          {downloadBusy ? <BELGIUM.Spinner size={14} /> : null}
          Download Both (KML + GPKG)
        </button>
      </div>
    </div>
  );
};
