window.BELGIUM = window.BELGIUM || {};

BELGIUM.App = function App() {
  const [user, setUser] = React.useState(BELGIUM.currentUser());
  const { activePage, selectedJobId, navigate } = BELGIUM.useAppRouter();
  const [showUpload, setShowUpload] = React.useState(false);
  const [jobsRefreshKey, setJobsRefreshKey] = React.useState(0);

  React.useEffect(() => {
    BELGIUM.onUnauthorized = () => setUser(null);
    return () => {
      BELGIUM.onUnauthorized = null;
    };
  }, []);

  function handleLogout() {
    BELGIUM.logout();
    setUser(null);
    BELGIUM.navigateToPage(BELGIUM.DEFAULT_PAGE);
  }

  if (!user) {
    return <BELGIUM.LoginPage onLogin={setUser} />;
  }

  function renderPage() {
    if (activePage === "chat") {
      return <BELGIUM.OllamaChatPage />;
    }
    if (activePage === "api-keys") {
      return <BELGIUM.ApiKeysPage />;
    }
    if (selectedJobId) {
      return (
        <BELGIUM.BelgiumProjectDetailPage
          user={user}
          jobId={selectedJobId}
          onBack={() => navigate("projects")}
        />
      );
    }
    return (
      <>
        <BELGIUM.BelgiumDashboardPage
          user={user}
          refreshKey={jobsRefreshKey}
          onOpenJob={(jobId) => navigate("projects", jobId)}
          onOpenUpload={() => setShowUpload(true)}
        />
        {showUpload && (
          <BELGIUM.BelgiumUploadModal
            onClose={() => setShowUpload(false)}
            onUploaded={(uploadedJobId) => {
              setShowUpload(false);
              setJobsRefreshKey((key) => key + 1);
              if (uploadedJobId) {
                navigate("projects", uploadedJobId);
              }
            }}
          />
        )}
      </>
    );
  }

  return (
    <BELGIUM.ErrorBoundary fallback="Unable to load the application shell. Please refresh and sign in again.">
      <BELGIUM.AppLayout
        activePage={activePage}
        onNavigate={navigate}
        user={user}
        onLogout={handleLogout}
      >
        {renderPage()}
      </BELGIUM.AppLayout>
    </BELGIUM.ErrorBoundary>
  );
};
