import React, { useEffect, useState } from "react";
import { BrowserRouter, useLocation, useNavigate } from "react-router-dom";
import LandingPage from "./pages/LandingPage";
import WorkspacePage from "./pages/WorkspacePage";
import "./styles/theme.css";
import "./styles/workspace.css";
import "./styles/landing.css";
import "./styles/authModal.css";

const App = () => (
  <BrowserRouter>
    <AppContent />
  </BrowserRouter>
);

const AppContent = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const showWorkspace = location.pathname === "/complaints";

  const [theme, setTheme] = useState(() => localStorage.getItem("qms-theme") || "dark");
  useEffect(() => {
    localStorage.setItem("qms-theme", theme);
  }, [theme]);

  const [currentUser, setCurrentUser] = useState(() => {
    try {
      const saved = localStorage.getItem("qms-user");
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  });

  const [authModal, setAuthModal] = useState(null);

  const handleAuthSuccess = (tokenData) => {
    localStorage.setItem("qms-user", JSON.stringify(tokenData));
    localStorage.setItem("qms-token", tokenData.access_token);
    setCurrentUser(tokenData);
    setAuthModal(null);
    navigate("/complaints");
  };

  const handleSignOut = () => {
    localStorage.removeItem("qms-user");
    localStorage.removeItem("qms-token");
    setCurrentUser(null);
    navigate("/");
  };

  if (showWorkspace) {
    return (
      <WorkspacePage
        theme={theme}
        setTheme={setTheme}
        currentUser={currentUser}
        onSignOut={handleSignOut}
      />
    );
  }

  return (
    <LandingPage
      theme={theme}
      setTheme={setTheme}
      authModal={authModal}
      setAuthModal={setAuthModal}
      onAuthSuccess={handleAuthSuccess}
    />
  );
};

export default App;
