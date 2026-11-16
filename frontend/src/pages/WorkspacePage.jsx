import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDispatch, useSelector } from "react-redux";
import CopilotChat from "../components/CopilotChat";
import ComplaintForm from "../components/ComplaintForm";
import { resetChat } from "../store/complaintSlice";
import {
  MyComplaintsView,
  BatchesProductionView,
  AnalyticsView,
  SettingsView,
} from "../components/WorkspaceModules";

const WorkspacePage = ({ theme, setTheme, currentUser, onSignOut }) => {
  const navigate = useNavigate();
  const dispatch = useDispatch();
  const { activeComplaint } = useSelector((state) => state.complaint);
  const [workspaceTab, setWorkspaceTab] = useState("log");

  useEffect(() => {
    document.documentElement.classList.add("qms-workspace-active");
    return () => {
      document.documentElement.classList.remove("qms-workspace-active");
    };
  }, []);

  return (
    <div className="qms-layout" data-theme={theme}>
      <aside className="qms-sidebar">
        <div className="qms-brand">
          <div className="qms-brand-mark">▣</div>
          <div>
            <strong>QMS Copilot</strong>
            <small>Quality Management System</small>
          </div>
        </div>
        <nav className="qms-nav" aria-label="Main navigation">
          <button
            className={workspaceTab === "log" ? "active" : ""}
            type="button"
            onClick={() => setWorkspaceTab("log")}
          >
            <span>＋</span>
            <span>Log Complaint</span>
          </button>
          <button
            className={workspaceTab === "my-complaints" ? "active" : ""}
            type="button"
            onClick={() => setWorkspaceTab("my-complaints")}
          >
            <span>▤</span>
            <span>My Complaints</span>
          </button>
          <button
            className={workspaceTab === "batches" ? "active" : ""}
            type="button"
            onClick={() => setWorkspaceTab("batches")}
          >
            <span>◈</span>
            <span>Batches &amp; Prod</span>
          </button>
          <button
            className={workspaceTab === "analytics" ? "active" : ""}
            type="button"
            onClick={() => setWorkspaceTab("analytics")}
          >
            <span>▥</span>
            <span>Analytics</span>
          </button>
          <button
            className={workspaceTab === "settings" ? "active" : ""}
            type="button"
            onClick={() => setWorkspaceTab("settings")}
          >
            <span>⚙</span>
            <span>Settings</span>
          </button>
        </nav>
        <div className="qms-sidebar-bottom">
          <div style={{ fontWeight: 700, color: "var(--c-dce3ed)" }}>◈ &nbsp; QMS</div>
          API &amp; FDF Quality Assurance
          <br />
          <button
            type="button"
            onClick={() => navigate("/")}
            style={{
              marginTop: 12,
              padding: 0,
              border: 0,
              background: "none",
              color: "var(--c-b6a9e4)",
              cursor: "pointer",
              fontSize: 11,
            }}
          >
            ← Back to Home
          </button>
        </div>
      </aside>

      <div className="qms-content">
        <header className="qms-topbar">
          <div className="qms-topbar-label">
            Quality Operations <span style={{ color: "var(--c-7467a9)" }}> / </span>{" "}
            {workspaceTab === "log"
              ? "Complaint Intake"
              : workspaceTab === "my-complaints"
              ? "Complaints & QA Investigation"
              : workspaceTab === "batches"
              ? "Batches & Production Records"
              : workspaceTab === "analytics"
              ? "Quality Analytics"
              : "Workspace Settings"}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <button
              className="qms-theme-toggle"
              type="button"
              onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            >
              <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
              {theme === "dark" ? "Light mode" : "Dark mode"}
            </button>
            <div className="qms-profile">
              <span style={{ fontSize: 17, color: "var(--c-b7addc)" }}>♧</span>
              <span className="qms-avatar">
                {currentUser?.full_name
                  ? currentUser.full_name
                      .split(" ")
                      .map((n) => n[0])
                      .join("")
                      .slice(0, 2)
                      .toUpperCase()
                  : "KS"}
              </span>
              <span>
                {currentUser?.full_name || "Quality Team"}
                <br />
                <small style={{ color: "var(--c-9eacc0)", fontWeight: 400 }}>
                  {currentUser?.role || "Workspace"}
                </small>
              </span>
            </div>
          </div>
        </header>

        <div className="qms-page-heading">
          <div>
            <h1>
              {workspaceTab === "log"
                ? "Log Customer Complaint"
                : workspaceTab === "my-complaints"
                ? "Logged Complaints & QA Investigation"
                : workspaceTab === "batches"
                ? "Batch Management & Production Stages"
                : workspaceTab === "analytics"
                ? "QMS Analytics Overview"
                : "Workspace & Account Settings"}
            </h1>
            <p>API &amp; FDF Quality Assurance Module</p>
          </div>
          {workspaceTab === "log" && (
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <button
                type="button"
                className="qms-theme-toggle"
                onClick={() => dispatch(resetChat())}
              >
                ＋ New Complaint
              </button>
              <span className="qms-page-tag">
                ● &nbsp; {activeComplaint?.status || "Pending Triage"}
              </span>
            </div>
          )}
        </div>

        {workspaceTab === "log" && (
          <div className="app-shell">
            <div className="chat-panel">
              <CopilotChat />
            </div>
            <div className="form-panel">
              <ComplaintForm />
            </div>
          </div>
        )}
        {workspaceTab === "my-complaints" && (
          <MyComplaintsView onOpenInCopilot={() => setWorkspaceTab("log")} />
        )}
        {workspaceTab === "batches" && <BatchesProductionView />}
        {workspaceTab === "analytics" && <AnalyticsView />}
        {workspaceTab === "settings" && (
          <SettingsView currentUser={currentUser} onSignOut={onSignOut} />
        )}
      </div>
    </div>
  );
};

export default WorkspacePage;
