import React, { useEffect, useState } from "react";
import axios from "axios";
import CustomerRegisterFields from "./CustomerRegisterFields";
import OrganizationRegisterFields from "./OrganizationRegisterFields";
import EmployeeRegisterFields from "./EmployeeRegisterFields";

const AUTH_API = "http://localhost:8000/api/auth";

const AuthModal = ({ authModal, setAuthModal, onAuthSuccess }) => {
  const [authNotice, setAuthNotice] = useState("");
  const [isChemist, setIsChemist] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [organizations, setOrganizations] = useState([]);
  const [authSubmitting, setAuthSubmitting] = useState(false);

  useEffect(() => {
    setAuthNotice("");
    if (authModal === "employee-register") {
      axios
        .get(`${AUTH_API}/organizations`)
        .then((res) => setOrganizations(res.data || []))
        .catch(() => setOrganizations([]));
    }
  }, [authModal]);

  if (!authModal) return null;

  const closeModal = () => {
    setAuthModal(null);
    setAuthNotice("");
    setIsChemist(false);
    setShowPassword(false);
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setAuthNotice("");
    const formData = new FormData(event.currentTarget);

    try {
      setAuthSubmitting(true);

      if (authModal === "customer-register") {
        if (formData.get("password") !== formData.get("confirmPassword")) {
          setAuthNotice("Passwords do not match. Please check and try again.");
          return;
        }
        const payload = {
          full_name: formData.get("full_name"),
          email: formData.get("email"),
          password: formData.get("password"),
          mobile_number: formData.get("mobile_number"),
          state: formData.get("state"),
          city: formData.get("city"),
          pincode: formData.get("pincode"),
          is_chemist: isChemist,
          pharmacy_name: isChemist ? formData.get("chemist_pharmacy_name") : null,
          chemist_pharmacy_name: isChemist ? formData.get("chemist_pharmacy_name") : null,
          pharmacy_type: isChemist ? formData.get("pharmacy_type") : null,
          drug_license_number: isChemist ? formData.get("drug_licence_number") : null,
          drug_licence_number: isChemist ? formData.get("drug_licence_number") : null,
          gst_number: isChemist ? formData.get("gst_number") : null,
          pharmacy_address: isChemist ? formData.get("pharmacy_address") : null,
        };
        const res = await axios.post(`${AUTH_API}/register/customer`, payload);
        onAuthSuccess(res.data);
        return;
      }

      if (authModal === "organization-register") {
        if (formData.get("admin_password") !== formData.get("confirm_admin_password")) {
          setAuthNotice("Administrator passwords do not match. Please check and try again.");
          return;
        }
        const payload = {
          organization_name: formData.get("organization_name"),
          trade_name: formData.get("trade_name") || formData.get("organization_name"),
          registration_number: formData.get("registration_number"),
          gst_number: formData.get("gst_number") || null,
          organization_type: formData.get("organization_type"),
          unit_name: formData.get("unit_name") || `${formData.get("city")} Main Plant`,
          unit_type: formData.get("unit_type") || "Both",
          official_email: formData.get("official_email"),
          company_mobile: formData.get("company_mobile"),
          registered_address: formData.get("registered_address"),
          country: formData.get("country") || "India",
          state: formData.get("state"),
          city: formData.get("city"),
          postal_code: formData.get("postal_code"),
          admin_full_name: formData.get("admin_full_name"),
          admin_designation: formData.get("admin_designation"),
          admin_email: formData.get("admin_email"),
          admin_password: formData.get("admin_password"),
          registration_code: formData.get("registration_code") || null,
        };
        const res = await axios.post(`${AUTH_API}/register/organization`, payload);
        onAuthSuccess(res.data);
        return;
      }

      if (authModal === "employee-register") {
        if (formData.get("password") !== formData.get("confirmPassword")) {
          setAuthNotice("Passwords do not match. Please check and try again.");
          return;
        }
        const payload = {
          full_name: formData.get("full_name"),
          email: formData.get("email"),
          password: formData.get("password"),
          mobile_number: formData.get("mobile_number"),
          organization_id: Number(formData.get("organization_id")),
          registration_code: formData.get("registration_code"),
          employee_id: formData.get("employee_id"),
          department: formData.get("department"),
          designation: formData.get("designation") || null,
          requested_role: formData.get("requested_role"),
        };
        const res = await axios.post(`${AUTH_API}/register/employee`, payload);
        onAuthSuccess(res.data);
        return;
      }

      if (authModal === "employee-login" || authModal === "customer-login") {
        const payload = {
          email: formData.get("email"),
          password: formData.get("password"),
        };
        const res = await axios.post(`${AUTH_API}/login`, payload);
        onAuthSuccess(res.data);
        return;
      }
    } catch (err) {
      const errMsg =
        err.response?.data?.detail ||
        (Array.isArray(err.response?.data?.detail)
          ? err.response.data.detail.map((d) => d.msg).join(", ")
          : "Could not reach backend server at http://localhost:8000.");
      setAuthNotice(String(errMsg));
    } finally {
      setAuthSubmitting(false);
    }
  };

  return (
    <div
      className="auth-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeModal();
      }}
    >
      <section className="auth-modal" role="dialog" aria-modal="true" aria-labelledby="auth-title">
        <button className="auth-close" type="button" aria-label="Close" onClick={closeModal}>
          ×
        </button>
        <div className="auth-mark">▣</div>

        {authModal === "portal-selection" ? (
          <>
            <h2 id="auth-title">Welcome to OJASYN</h2>
            <p className="auth-subtitle">Choose how you want to continue.</p>
            <div className="portal-choice-list">
              <button
                className="portal-choice customer-choice"
                type="button"
                onClick={() => setAuthModal("customer-login")}
              >
                <span className="portal-choice-icon">♙</span>
                <span className="portal-choice-copy">
                  <strong>Customer Portal</strong>
                  <small>
                    For customers, patients, chemists and distributors who want to submit or track product complaints.
                  </small>
                </span>
                <span className="portal-choice-arrow">→</span>
              </button>
              <button
                className="portal-choice employee-choice"
                type="button"
                onClick={() => setAuthModal("employee-login")}
              >
                <span className="portal-choice-icon">▥</span>
                <span className="portal-choice-copy">
                  <strong>Organization Portal</strong>
                  <small>For company owners, administrators, QA, QC, Production and other employees.</small>
                </span>
                <span className="portal-choice-arrow">→</span>
              </button>
            </div>
            <p className="auth-switch">
              External customer complaints can also be submitted through the public complaint portal.
            </p>
          </>
        ) : (
          <>
            <h2 id="auth-title">
              {authModal === "customer-login"
                ? "Customer Sign In"
                : authModal === "customer-register"
                ? "Create Customer Account"
                : authModal === "employee-login"
                ? "Organization Sign In"
                : authModal === "employee-register"
                ? "Employee Registration"
                : "Register Your Organization"}
            </h2>
            <p className="auth-subtitle">
              {authModal === "customer-login"
                ? "Sign in to submit and track your product complaints."
                : authModal === "customer-register"
                ? "Create an account to manage your complaint history."
                : authModal === "employee-login"
                ? "Sign in with your organization-provided employee account."
                : authModal === "employee-register"
                ? "Join your pharmaceutical organization with your company invite code."
                : "Submit your company details to begin organization onboarding."}
            </p>

            <form className="auth-form" onSubmit={handleSubmit}>
              {authModal === "customer-register" && (
                <CustomerRegisterFields isChemist={isChemist} setIsChemist={setIsChemist} />
              )}

              {authModal === "organization-register" && (
                <OrganizationRegisterFields showPassword={showPassword} setShowPassword={setShowPassword} />
              )}

              {authModal === "employee-register" && <EmployeeRegisterFields organizations={organizations} />}

              {authModal !== "organization-register" && (
                <label className="auth-field">
                  {authModal === "employee-login" || authModal === "employee-register"
                    ? "Work email address"
                    : "Email address"}
                  <input
                    type="email"
                    name="email"
                    placeholder={authModal.startsWith("employee") ? "you@company.com" : "you@example.com"}
                    autoComplete="email"
                    required
                  />
                </label>
              )}

              {authModal !== "organization-register" && (
                <label className="auth-field">
                  Password
                  <div className="password-input-wrap">
                    <input
                      type={showPassword ? "text" : "password"}
                      name="password"
                      placeholder="Enter your password (min 8 chars)"
                      autoComplete={authModal.endsWith("login") ? "current-password" : "new-password"}
                      minLength={authModal.endsWith("login") ? "6" : "8"}
                      required
                    />
                    <button
                      type="button"
                      className="password-toggle"
                      onClick={() => setShowPassword((value) => !value)}
                    >
                      {showPassword ? "Hide" : "Show"}
                    </button>
                  </div>
                </label>
              )}

              {(authModal === "customer-register" || authModal === "employee-register") && (
                <label className="auth-field">
                  Confirm password
                  <input
                    type="password"
                    name="confirmPassword"
                    placeholder="Re-enter your password"
                    autoComplete="new-password"
                    minLength="8"
                    required
                  />
                </label>
              )}

              {(authModal === "customer-login" || authModal === "employee-login") && (
                <button
                  className="auth-forgot"
                  type="button"
                  onClick={() =>
                    setAuthNotice("Contact your Organization Administrator or support to reset your password.")
                  }
                >
                  Forgot password?
                </button>
              )}

              <button className="auth-submit" type="submit" disabled={authSubmitting}>
                {authSubmitting
                  ? "Processing..."
                  : authModal === "customer-login" || authModal === "employee-login"
                  ? "Sign in →"
                  : "Submit registration →"}
              </button>

              {authNotice && (
                <div className="auth-notice" role="status">
                  {authNotice}
                </div>
              )}
            </form>

            <div className="auth-switch">
              {authModal === "customer-login" ? (
                <>
                  Don't have a customer account?{" "}
                  <button
                    type="button"
                    onClick={() => {
                      setIsChemist(false);
                      setAuthModal("customer-register");
                    }}
                  >
                    Register
                  </button>
                </>
              ) : null}
              {authModal === "customer-register" ? (
                <>
                  Already have a customer account?{" "}
                  <button type="button" onClick={() => setAuthModal("customer-login")}>
                    Sign in
                  </button>
                </>
              ) : null}
              {authModal === "employee-login" ? (
                <>
                  New organization?{" "}
                  <button type="button" onClick={() => setAuthModal("organization-register")}>
                    Register company
                  </button>
                  {" · "}
                  Have an invite code?{" "}
                  <button type="button" onClick={() => setAuthModal("employee-register")}>
                    Register as employee
                  </button>
                </>
              ) : null}
              {authModal === "organization-register" || authModal === "employee-register" ? (
                <>
                  Already registered?{" "}
                  <button type="button" onClick={() => setAuthModal("employee-login")}>
                    Employee sign in
                  </button>
                  {authModal === "organization-register" ? (
                    <>
                      {" "}
                      ·{" "}
                      <button type="button" onClick={() => setAuthModal("employee-register")}>
                        Join existing company
                      </button>
                    </>
                  ) : (
                    <>
                      {" "}
                      ·{" "}
                      <button type="button" onClick={() => setAuthModal("organization-register")}>
                        Register new company
                      </button>
                    </>
                  )}
                </>
              ) : null}
            </div>
            <button
              className="auth-back"
              type="button"
              onClick={() => {
                setIsChemist(false);
                setShowPassword(false);
                setAuthModal("portal-selection");
              }}
            >
              ← Back to portal selection
            </button>
          </>
        )}
      </section>
    </div>
  );
};

export default AuthModal;
