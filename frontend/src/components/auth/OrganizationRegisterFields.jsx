import React from "react";

const OrganizationRegisterFields = ({ showPassword, setShowPassword }) => (
  <>
    <div className="auth-notice">
      Register your company, primary manufacturing unit, and first Organization Administrator account.
    </div>

    <p className="chemist-fields-title">Organization details</p>
    <label className="auth-field">
      Legal organization name
      <input type="text" name="organization_name" placeholder="Enter registered company name" autoComplete="organization" required />
    </label>
    <label className="auth-field">
      Trade / brand name (optional)
      <input type="text" name="trade_name" placeholder="Enter trade name if different" />
    </label>
    <label className="auth-field">
      Organization registration number (CIN)
      <input type="text" name="registration_number" placeholder="Company / incorporation registration number" required />
    </label>
    <label className="auth-field">
      GST number (optional)
      <input type="text" name="gst_number" placeholder="Enter GSTIN, if applicable" />
    </label>
    <label className="auth-field">
      Organization type
      <select name="organization_type" defaultValue="" required>
        <option value="" disabled>Select organization type</option>
        <option value="pharmaceutical_manufacturer">Pharmaceutical manufacturer</option>
        <option value="pharmaceutical_distributor">Pharmaceutical distributor</option>
        <option value="healthcare">Healthcare organization</option>
        <option value="other">Other</option>
      </select>
    </label>
    <label className="auth-field">
      Primary manufacturing unit name
      <input type="text" name="unit_name" placeholder="e.g. Pune Main Plant / Unit I" required />
    </label>
    <label className="auth-field">
      Manufacturing unit type (API / FDF)
      <select name="unit_type" defaultValue="Both" required>
        <option value="FDF">FDF (Finished Dosage Form)</option>
        <option value="API">API (Active Pharmaceutical Ingredient)</option>
        <option value="Both">Both (API &amp; FDF)</option>
      </select>
    </label>

    <p className="chemist-fields-title">Company contact details</p>
    <label className="auth-field">
      Official company email
      <input type="email" name="official_email" placeholder="quality@company.com" autoComplete="email" required />
    </label>
    <label className="auth-field">
      Company contact number
      <input
        type="tel"
        name="company_mobile"
        placeholder="Enter company contact number"
        inputMode="tel"
        pattern="[0-9+() -]{8,16}"
        required
      />
    </label>
    <label className="auth-field">
      Registered address
      <textarea className="chemist-address" name="registered_address" placeholder="Enter complete registered address" required />
    </label>
    <label className="auth-field">
      Country
      <input type="text" name="country" defaultValue="India" autoComplete="country-name" required />
    </label>
    <label className="auth-field">
      State
      <input type="text" name="state" placeholder="Enter state" autoComplete="address-level1" required />
    </label>
    <label className="auth-field">
      City
      <input type="text" name="city" placeholder="Enter city" autoComplete="address-level2" required />
    </label>
    <label className="auth-field">
      PIN code
      <input type="text" name="postal_code" placeholder="Enter PIN code" inputMode="numeric" pattern="[0-9]{6}" required />
    </label>

    <p className="chemist-fields-title">Primary organization administrator</p>
    <label className="auth-field">
      Administrator full name
      <input type="text" name="admin_full_name" placeholder="Enter administrator's full name" autoComplete="name" required />
    </label>
    <label className="auth-field">
      Designation
      <input type="text" name="admin_designation" placeholder="e.g. Company Owner / Quality Head" required />
    </label>
    <label className="auth-field">
      Employee registration invite code (optional)
      <input type="text" name="registration_code" placeholder="e.g. OJASYN-QA-2026 (auto-generated if blank)" />
    </label>
    <label className="auth-field">
      Administrator login email
      <input type="email" name="admin_email" placeholder="admin@company.com" autoComplete="username" required />
    </label>
    <label className="auth-field">
      Create administrator password
      <div className="password-input-wrap">
        <input
          type={showPassword ? "text" : "password"}
          name="admin_password"
          placeholder="Create a strong password (min 8 chars)"
          autoComplete="new-password"
          minLength="8"
          required
        />
        <button type="button" className="password-toggle" onClick={() => setShowPassword((value) => !value)}>
          {showPassword ? "Hide" : "Show"}
        </button>
      </div>
    </label>
    <label className="auth-field">
      Confirm administrator password
      <input
        type="password"
        name="confirm_admin_password"
        placeholder="Re-enter administrator password"
        autoComplete="new-password"
        minLength="8"
        required
      />
    </label>
  </>
);

export default OrganizationRegisterFields;
