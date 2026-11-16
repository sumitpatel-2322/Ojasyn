import React from "react";
import AuthModal from "../components/auth/AuthModal";

const LandingPage = ({ theme, setTheme, authModal, setAuthModal, onAuthSuccess }) => {
  return (
    <div className="qms-app" data-theme={theme}>
      <div className="landing">
        {/* NAVBAR */}
        <nav className="navbar">
          <div className="logo">
            <div className="logo-icon">▣</div>
            QMS<span className="logo-dot">.</span>
          </div>

          <div className="nav-links">
            <a href="#platform">Platform</a>
            <a href="#capabilities">Capabilities</a>
            <a href="#workflow">How it works</a>
          </div>

          <div className="nav-actions">
            <button
              className="landing-theme-toggle"
              type="button"
              onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            >
              <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
              {theme === "dark" ? "Light" : "Dark"}
            </button>
            <button
              className="sign-btn"
              onClick={() => setAuthModal("portal-selection")}
            >
              Sign in
            </button>
            <button
              className="start-btn"
              onClick={() => setAuthModal("portal-selection")}
            >
              Get started ↗
            </button>
          </div>
        </nav>

        {/* HERO SECTION */}
        <section className="hero" id="platform">
          <div className="hero-content">
            <div className="badge">
              <span className="badge-dot"></span>
              AI-POWERED QUALITY OPERATIONS
            </div>

            <h1>
              Quality complaints,
              <br />
              <span className="gradient-text">handled intelligently.</span>
            </h1>

            <p className="hero-description">
              Bring complaint intake, product identification and risk assessment
              into one clear workflow. Let AI help your quality team turn
              unstructured reports into review-ready records.
            </p>

            <div className="hero-buttons">
              <button
                className="hero-btn primary-btn"
                onClick={() => setAuthModal("portal-selection")}
              >
                Explore the platform →
              </button>

              <button
                className="hero-btn secondary-btn"
                onClick={() =>
                  document.getElementById("workflow").scrollIntoView({
                    behavior: "smooth",
                  })
                }
              >
                See how it works
              </button>
            </div>

            <div className="trust-text">
              <span>♧</span>
              Human review stays in control at every critical step.
            </div>
          </div>

          {/* DASHBOARD MOCKUP */}
          <div className="dashboard-wrap">
            <div className="dashboard">
              <div className="dash-top">
                <div className="dash-dots">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
                QMS · QUALITY ASSURANCE WORKSPACE
                <div className="dash-avatar"></div>
              </div>

              <div className="dash-body">
                <aside className="sidebar">
                  <div className="sidebar-title">
                    QMS
                    <br />
                    WORKSPACE
                  </div>

                  <div className="side-item active">✦ &nbsp; New complaint</div>
                  <div className="side-item">▤ &nbsp; Complaints</div>
                  <div className="side-item">◷ &nbsp; Triage queue</div>
                  <div className="side-item">▦ &nbsp; Reports</div>
                  <div className="side-item">⌘ &nbsp; Analytics</div>
                  <div className="side-item">⚙ &nbsp; Settings</div>
                </aside>

                <main className="dash-main">
                  <div className="dash-heading">
                    <div>
                      <h3>Quality overview</h3>
                      <p className="dash-subtitle">
                        Complaint intake &amp; triage workspace
                      </p>
                    </div>

                    <span className="ready">● System ready</span>
                  </div>

                  <div className="stats">
                    <div className="stat-card">
                      <small>Open complaints</small>
                      <h2>24</h2>
                      <p>↑ Active queue</p>
                    </div>

                    <div className="stat-card">
                      <small>Awaiting triage</small>
                      <h2>08</h2>
                      <p>Needs review</p>
                    </div>

                    <div className="stat-card">
                      <small>Reviewed today</small>
                      <h2>12</h2>
                      <p>Review activity</p>
                    </div>
                  </div>

                  <div className="dash-bottom">
                    <div className="process-card">
                      <h4>Complaint processing</h4>

                      {[
                        ["Intake", 88],
                        ["Identification", 72],
                        ["Risk review", 54],
                        ["Ready to log", 36],
                      ].map(([name, value]) => (
                        <div className="progress-row" key={name}>
                          <span>{name}</span>

                          <div className="progress">
                            <div
                              className="progress-fill"
                              style={{ width: `${value}%` }}
                            ></div>
                          </div>

                          <strong>{value}%</strong>
                        </div>
                      ))}
                    </div>

                    <div className="risk-card">
                      <h4>AI risk assessment</h4>
                      <div className="risk-circle">M</div>
                      <div className="risk-label">Medium priority</div>
                      <div className="risk-sub">Illustrative assessment</div>
                    </div>
                  </div>
                </main>
              </div>
            </div>

            <div className="floating-card">
              <strong>✓ Review-ready workflow</strong>
              <p>AI-assisted · Human verified</p>
            </div>
          </div>
        </section>

        {/* CAPABILITIES */}
        <section className="section" id="capabilities">
          <div className="section-label">PLATFORM CAPABILITIES</div>
          <h2>Everything your quality team needs.</h2>
          <p className="section-description">
            Simplify complaint management with intelligent automation,
            structured data and human-controlled quality decisions.
          </p>

          <div className="feature-grid">
            <div className="feature-card">
              <div className="feature-icon">✦</div>
              <h3>AI Complaint Intake</h3>
              <p>
                Convert unstructured customer complaints into structured and
                review-ready information.
              </p>
            </div>

            <div className="feature-card">
              <div className="feature-icon">⌕</div>
              <h3>Product Identification</h3>
              <p>
                Identify relevant product information and organize complaint
                details efficiently.
              </p>
            </div>

            <div className="feature-card">
              <div className="feature-icon">◈</div>
              <h3>Risk Assessment</h3>
              <p>
                Support quality teams with AI-assisted risk assessment and
                review workflows.
              </p>
            </div>
          </div>
        </section>

        {/* HOW IT WORKS */}
        <section className="section" id="workflow">
          <div className="section-label">OUR WORKFLOW</div>
          <h2>How QMS works</h2>
          <p className="section-description">
            A simple workflow from complaint submission to quality review.
          </p>

          <div className="workflow">
            <div className="step">
              <span>STEP 01</span>
              <h3>Complaint Intake</h3>
              <p>
                Collect customer complaint information through a structured
                process.
              </p>
            </div>

            <div className="step">
              <span>STEP 02</span>
              <h3>AI Processing</h3>
              <p>
                Extract and organize important information from the complaint.
              </p>
            </div>

            <div className="step">
              <span>STEP 03</span>
              <h3>Risk Assessment</h3>
              <p>
                Analyze complaint details and support quality risk evaluation.
              </p>
            </div>

            <div className="step">
              <span>STEP 04</span>
              <h3>Human Review</h3>
              <p>
                Quality professionals verify the information before final
                decisions.
              </p>
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="cta">
          <div className="section-label">GET STARTED</div>
          <h2>Make quality management smarter.</h2>
          <p>
            Bring your complaint management workflow into one intelligent
            workspace.
          </p>

          <button
            className="hero-btn primary-btn"
            onClick={() => setAuthModal("portal-selection")}
          >
            Explore QMS Workspace ↗
          </button>
        </section>

        {/* FOOTER */}
        <footer className="footer">
          <div>© 2026 QMS. All rights reserved.</div>
          <div>
            <a href="#platform">Platform</a>
            <a href="#capabilities">Capabilities</a>
            <a href="#workflow">Workflow</a>
          </div>
        </footer>

        <AuthModal
          authModal={authModal}
          setAuthModal={setAuthModal}
          onAuthSuccess={onAuthSuccess}
        />
      </div>
    </div>
  );
};

export default LandingPage;
