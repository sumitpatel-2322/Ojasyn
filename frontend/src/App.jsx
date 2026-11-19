import React, { useEffect, useState } from "react";
import { BrowserRouter, useLocation, useNavigate } from "react-router-dom";
import LandingPage from "./pages/LandingPage";
import WorkspacePage from "./pages/WorkspacePage";
import QADashboard from "./QADashboard";
import OrganizationDashboard from "./OrganizationDashboard";
import ManufacturerDashboard from "./ManufacturerDashboard";
import CustomerDashboard from "./CustomerDashboard";
import ChemistDashboard from "./ChemistDashboard";
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
  const showQADashboard = location.pathname === "/qa-dashboard";
  const showOrganizationDashboard = location.pathname === "/organization-dashboard";
  const showManufacturerDashboard = location.pathname === "/manufacturer-dashboard";
  const showCustomerDashboard = location.pathname === "/customer-dashboard";
  const showChemistDashboard = location.pathname === "/chemist-dashboard";

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
    if (tokenData.access_token) {
      localStorage.setItem("qms-token", tokenData.access_token);
      localStorage.setItem("qms-access-token", tokenData.access_token);
    }
    if (tokenData.email) {
      localStorage.setItem("qms-user-email", String(tokenData.email).trim().toLowerCase());
    }
    if (tokenData.full_name !== undefined) {
      localStorage.setItem("qms-user-name", tokenData.full_name || "");
    }
    if (tokenData.role !== undefined) {
      localStorage.setItem("qms-user-role", tokenData.role || "");
    }
    if (tokenData.designation !== undefined) {
      localStorage.setItem("qms-user-designation", tokenData.designation || "");
    }
    if (tokenData.user_id !== undefined && tokenData.user_id !== null) {
      localStorage.setItem("qms-user-id", String(tokenData.user_id));
    }

    setCurrentUser(tokenData);
    setAuthModal(null);

    const normalizedRole = String(tokenData.role || "").toLowerCase().replaceAll(" ", "_");
    const targetDashboard =
      normalizedRole === "chemist"
        ? "/chemist-dashboard"
        : normalizedRole === "customer"
          ? "/customer-dashboard"
          : tokenData.dashboard_path || "/complaints";

    navigate(targetDashboard, { replace: true });
  };

  const handleSignOut = () => {
    [
      "qms-user",
      "qms-token",
      "qms-access-token",
      "qms-user-email",
      "qms-user-name",
      "qms-user-role",
      "qms-user-designation",
      "qms-user-id",
    ].forEach((key) => localStorage.removeItem(key));
    setCurrentUser(null);
    navigate("/");
  };

  if (showQADashboard) {
    return <QADashboard />;
  }

  if (showOrganizationDashboard) {
    return <OrganizationDashboard />;
  }

  if (showManufacturerDashboard) {
    return <ManufacturerDashboard />;
  }

  if (showCustomerDashboard) {
    return <CustomerDashboard />;
  }

  if (showChemistDashboard) {
    return <ChemistDashboard />;
  }

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
