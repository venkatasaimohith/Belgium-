window.BELGIUM = window.BELGIUM || {};

BELGIUM.DEFAULT_PAGE = "projects";
BELGIUM.VALID_PAGES = new Set(["projects", "chat", "api-keys"]);

BELGIUM.parseHashRoute = function parseHashRoute() {
  const raw = String(window.location.hash || "").replace(/^#/, "").trim();
  const parts = raw.split("/").filter(Boolean);
  const root = (parts[0] || BELGIUM.DEFAULT_PAGE).toLowerCase();

  if (root === "projects" && parts[1]) {
    return { page: "projects", jobId: parts[1] };
  }
  if (BELGIUM.VALID_PAGES.has(root)) {
    return { page: root, jobId: null };
  }
  return { page: BELGIUM.DEFAULT_PAGE, jobId: null };
};

BELGIUM.getPageFromHash = function getPageFromHash() {
  return BELGIUM.parseHashRoute().page;
};

BELGIUM.getJobIdFromHash = function getJobIdFromHash() {
  return BELGIUM.parseHashRoute().jobId;
};

BELGIUM.navigateToPage = function navigateToPage(page, jobId) {
  const normalized = BELGIUM.VALID_PAGES.has(page) ? page : BELGIUM.DEFAULT_PAGE;
  const nextHash = jobId && normalized === "projects" ? `${normalized}/${jobId}` : normalized;
  if (window.location.hash !== `#${nextHash}`) {
    window.location.hash = nextHash;
  }
  return { page: normalized, jobId: jobId || null };
};

BELGIUM.useAppRouter = function useAppRouter() {
  const [route, setRoute] = React.useState(BELGIUM.parseHashRoute());

  React.useEffect(() => {
    if (!window.location.hash) {
      BELGIUM.navigateToPage(BELGIUM.DEFAULT_PAGE);
    }

    function onHashChange() {
      setRoute(BELGIUM.parseHashRoute());
    }

    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  function navigate(page, jobId) {
    const next = BELGIUM.navigateToPage(page, jobId);
    setRoute(next);
    return next;
  }

  return {
    activePage: route.page,
    selectedJobId: route.jobId,
    navigate,
  };
};
