window.BELGIUM = window.BELGIUM || {};

BELGIUM.BelgiumRecordsTable = function BelgiumRecordsTable({
  job,
  records,
  onRefresh,
  onViewMap,
  onOpenLogs,
  onToggleSidebar,
  showSidebar,
}) {
  const [searchInput, setSearchInput] = React.useState("");
  const [pageSize, setPageSize] = React.useState(25);
  const [currentPage, setCurrentPage] = React.useState(1);
  const [gotoPageInput, setGotoPageInput] = React.useState("");

  const isJobNotRun = !job || job.status === "UPLOADED" || job.status === "PENDING";

  const COLUMNS = [
    { key: "#", label: "#" },
    { key: "fid", label: "fid" },
    { key: "loc_id", label: "loc_id" },
    { key: "bm_street_name", label: "bm_street_name" },
    { key: "bm_housenumber", label: "bm_housenumber" },
    { key: "bm_box", label: "bm_box" },
    { key: "bm_subnumber", label: "bm_subnumber" },
    { key: "bm_address", label: "bm_address" },
    { key: "m_municipality_name", label: "m_municipality_name" },
    { key: "bm_normalized", label: "bm_normalized" },
    { key: "name", label: "name" },
    { key: "Check", label: "Check" },
    { key: "Building Type", label: "Building Type" },
    { key: "Remarks", label: "Remarks" },
    { key: "Number of Floors", label: "Number of Floors" },
  ];

  const rows = React.useMemo(() => {
    if (Array.isArray(records) && records.length) return records;
    return [];
  }, [records]);

  const filteredRows = React.useMemo(() => {
    const q = searchInput.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) =>
      [
        r.fid,
        r.loc_id,
        r.bm_street_name,
        r.bm_housenumber,
        r.bm_box,
        r.bm_subnumber,
        r.bm_address,
        r.m_municipality_name,
        r.bm_normalized,
        r.name,
        r.Check,
        r["Building Type"],
        r.Remarks,
      ]
        .map((v) => String(v ?? "").toLowerCase())
        .some((v) => v.includes(q))
    );
  }, [rows, searchInput]);

  React.useEffect(() => {
    setCurrentPage(1);
  }, [searchInput, pageSize]);

  const totalRows = filteredRows.length;
  const totalPages = Math.max(1, Math.ceil(totalRows / pageSize) || 1);
  const activePage = Math.min(currentPage, totalPages);
  const startIndex = (activePage - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageSize, totalRows);
  const paginatedRows = React.useMemo(
    () => filteredRows.slice(startIndex, endIndex),
    [filteredRows, startIndex, endIndex]
  );

  const pageNumbers = React.useMemo(() => {
    const pages = [];
    const maxVisible = 5;
    let start = Math.max(1, activePage - 2);
    let end = Math.min(totalPages, start + maxVisible - 1);
    if (end - start + 1 < maxVisible) start = Math.max(1, end - maxVisible + 1);
    for (let p = start; p <= end; p++) pages.push(p);
    return pages;
  }, [activePage, totalPages]);

  const handleGotoPageSubmit = (e) => {
    e.preventDefault();
    const target = parseInt(gotoPageInput, 10);
    if (!isNaN(target) && target >= 1 && target <= totalPages) {
      setCurrentPage(target);
      setGotoPageInput("");
    }
  };

  const cellNull = (value) => {
    if (value === null || value === undefined || value === "") {
      return <span className="italic text-slate-400 dark:text-slate-500">NULL</span>;
    }
    return value;
  };

  const checkBadge = (value) => {
    if (value === null || value === undefined || value === "") {
      return cellNull(null);
    }
    const v = String(value).toUpperCase();
    const cls =
      v === "VALID"
        ? "bg-emerald-500/20 text-emerald-800 dark:text-emerald-300 border-emerald-500/30"
        : v === "NEW"
        ? "bg-sky-500/20 text-sky-800 dark:text-sky-300 border-sky-500/30"
        : v === "TBC"
        ? "bg-amber-500/20 text-amber-800 dark:text-amber-300 border-amber-500/30"
        : "bg-slate-200 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border-slate-300 dark:border-slate-700";
    return (
      <span className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-bold ${cls}`}>
        {v}
      </span>
    );
  };

  const buildingBadge = (value) => {
    if (value === null || value === undefined || value === "") return cellNull(null);
    const v = String(value).toUpperCase();
    const cls =
      v === "MDU"
        ? "bg-violet-500/15 text-violet-800 dark:text-violet-300"
        : "bg-slate-200/80 dark:bg-slate-800 text-slate-800 dark:text-slate-200";
    return (
      <span className={`inline-flex rounded px-2 py-0.5 text-[11px] font-bold ${cls}`}>{v}</span>
    );
  };

  return (
    <div className="records-table-root p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-bold text-slate-900 dark:text-white">
              Address Landbase Records
            </h2>
            {isJobNotRun && (
              <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-500/20">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse"></span>
                Pipeline not run — Check column empty
              </span>
            )}
          </div>
          <p className="text-xs mt-0.5" style={{ color: "var(--app-text-soft)" }}>
            fid, street, house, box, municipality, Check (VALID/NEW/TBC), Building Type, Remarks
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <input
              type="search"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search street, Check, Building Type..."
              className="app-input rounded-lg px-3 py-1.5 text-xs min-w-[240px]"
            />
          </div>
          <div className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400">
            <span>Show:</span>
            <select
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value))}
              className="app-input rounded-lg px-2 py-1 text-xs"
            >
              <option value={10}>10 rows</option>
              <option value={25}>25 rows</option>
              <option value={50}>50 rows</option>
              <option value={100}>100 rows</option>
            </select>
          </div>
          <button
            type="button"
            className="app-button app-btn-secondary rounded-lg px-3 py-1.5 text-xs font-semibold"
            onClick={onRefresh}
          >
            Refresh
          </button>
          {onToggleSidebar && (
            <button
              type="button"
              onClick={onToggleSidebar}
              className="app-button app-btn-secondary rounded-lg px-2.5 py-1.5 text-xs font-semibold flex items-center gap-1.5"
            >
              <BELGIUM.Icon name="layout" size={14} />
              <span>{showSidebar ? "Hide Sidebar" : "Show Sidebar"}</span>
            </button>
          )}
        </div>
      </div>

      <div
        className="overflow-x-auto rounded-xl border bg-white/80 dark:bg-slate-950/40"
        style={{ borderColor: "var(--app-border)" }}
      >
        <table className="w-full min-w-[1400px] text-left text-xs">
          <thead>
            <tr className="border-b bg-slate-100 dark:bg-slate-900/80 text-[11px] font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-400">
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  className={`whitespace-nowrap px-3 py-2.5 ${
                    col.key === "Check" || col.key === "Building Type" ? "text-center" : ""
                  }`}
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
            {paginatedRows.length === 0 ? (
              <tr>
                <td colSpan={COLUMNS.length} className="px-4 py-8 text-center text-slate-500 text-xs">
                  {searchInput
                    ? `No records match "${searchInput}".`
                    : "No records loaded. Upload a GPKG/KML to begin."}
                </td>
              </tr>
            ) : (
              paginatedRows.map((row, idx) => {
                const absIndex = startIndex + idx + 1;
                const zebra = idx % 2 === 1 ? "bg-slate-50/80 dark:bg-slate-900/30" : "";
                return (
                  <tr key={row.id || absIndex} className={`hover:bg-cyan-500/5 transition ${zebra}`}>
                    <td className="px-3 py-2 text-slate-500">{absIndex}</td>
                    <td className="px-3 py-2 font-mono text-slate-800 dark:text-slate-200">
                      {cellNull(row.fid)}
                    </td>
                    <td className="px-3 py-2 font-mono">{cellNull(row.loc_id)}</td>
                    <td className="px-3 py-2 font-medium text-slate-900 dark:text-slate-100">
                      {cellNull(row.bm_street_name)}
                    </td>
                    <td className="px-3 py-2">{cellNull(row.bm_housenumber)}</td>
                    <td className="px-3 py-2">{cellNull(row.bm_box)}</td>
                    <td className="px-3 py-2">{cellNull(row.bm_subnumber)}</td>
                    <td className="px-3 py-2 font-semibold">{cellNull(row.bm_address)}</td>
                    <td className="px-3 py-2">{cellNull(row.m_municipality_name)}</td>
                    <td className="px-3 py-2 font-mono text-[11px]">{cellNull(row.bm_normalized)}</td>
                    <td className="px-3 py-2">{cellNull(row.name)}</td>
                    <td className="px-3 py-2 text-center">{checkBadge(row.Check)}</td>
                    <td className="px-3 py-2 text-center">{buildingBadge(row["Building Type"])}</td>
                    <td className="px-3 py-2 max-w-[220px] truncate" title={row.Remarks || ""}>
                      {cellNull(row.Remarks)}
                    </td>
                    <td className="px-3 py-2 text-center">{cellNull(row["Number of Floors"])}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-600 dark:text-slate-400 border-t border-slate-200 dark:border-slate-800/80 pt-3">
        <div className="flex items-center gap-3">
          {totalRows > 0 ? (
            <span>
              Showing <strong className="text-slate-900 dark:text-slate-200">{startIndex + 1}</strong> to{" "}
              <strong className="text-slate-900 dark:text-slate-200">{endIndex}</strong> of{" "}
              <strong className="text-slate-900 dark:text-slate-200">
                {totalRows.toLocaleString()}
              </strong>{" "}
              entries
            </span>
          ) : (
            <span>No entries to display</span>
          )}
          <span className="text-slate-400">|</span>
          <span>
            Page <strong className="text-slate-900 dark:text-slate-200">{activePage}</strong> of{" "}
            <strong className="text-slate-900 dark:text-slate-200">{totalPages}</strong>
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <form onSubmit={handleGotoPageSubmit} className="flex items-center gap-1 text-xs">
            <span>Go to page:</span>
            <input
              type="number"
              min="1"
              max={totalPages}
              value={gotoPageInput}
              onChange={(e) => setGotoPageInput(e.target.value)}
              placeholder={String(activePage)}
              className="app-input rounded px-2 py-0.5 text-xs w-12 text-center"
            />
            <button type="submit" className="app-button app-btn-secondary px-2 py-0.5 rounded text-xs">
              Go
            </button>
          </form>
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={activePage === 1}
              onClick={() => setCurrentPage(1)}
              className="app-button app-btn-secondary px-2.5 py-1 rounded text-xs font-semibold disabled:opacity-30"
            >
              ⏮ First
            </button>
            <button
              type="button"
              disabled={activePage === 1}
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              className="app-button app-btn-secondary px-2.5 py-1 rounded text-xs font-semibold disabled:opacity-30"
            >
              ◀ Prev
            </button>
            {pageNumbers.map((pageNum) => (
              <button
                key={pageNum}
                type="button"
                onClick={() => setCurrentPage(pageNum)}
                className={`px-3 py-1 rounded text-xs font-semibold transition ${
                  pageNum === activePage
                    ? "bg-cyan-500 text-slate-950 font-bold"
                    : "bg-slate-200 dark:bg-slate-800 text-slate-800 dark:text-slate-300"
                }`}
              >
                {pageNum}
              </button>
            ))}
            <button
              type="button"
              disabled={activePage === totalPages}
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              className="app-button app-btn-secondary px-2.5 py-1 rounded text-xs font-semibold disabled:opacity-30"
            >
              Next ▶
            </button>
            <button
              type="button"
              disabled={activePage === totalPages}
              onClick={() => setCurrentPage(totalPages)}
              className="app-button app-btn-secondary px-2.5 py-1 rounded text-xs font-semibold disabled:opacity-30"
            >
              Last ⏭
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
