import React from "react";

const EmployeeRegisterFields = ({ organizations }) => (
  <>
    <div className="auth-notice">
      Register as QA, QC, or Production personnel under an existing organization.
    </div>
    <label className="auth-field">
      Full name
      <input type="text" name="full_name" placeholder="Enter your full name" autoComplete="name" required />
    </label>
    <label className="auth-field">
      Mobile number
      <input
        type="tel"
        name="mobile_number"
        placeholder="Enter your mobile number"
        inputMode="tel"
        pattern="[0-9+() -]{8,16}"
        required
      />
    </label>
    <label className="auth-field">
      Organization
      <select name="organization_id" defaultValue="" required>
        <option value="" disabled>Select your organization</option>
        {organizations.map((org) => (
          <option key={org.id} value={org.id}>
            #{org.id} — {org.legal_name} ({org.city || org.country || "India"})
          </option>
        ))}
      </select>
    </label>
    <label className="auth-field">
      Company registration invite code
      <input type="text" name="registration_code" placeholder="Enter invite code shared by your Admin" required />
    </label>
    <label className="auth-field">
      Employee ID
      <input type="text" name="employee_id" placeholder="e.g. EMP-QA-104" required />
    </label>
    <label className="auth-field">
      Department
      <input type="text" name="department" placeholder="e.g. Quality Assurance / Production" required />
    </label>
    <label className="auth-field">
      Designation
      <input type="text" name="designation" placeholder="e.g. Senior QA Executive" />
    </label>
    <label className="auth-field">
      Role in QMS
      <select name="requested_role" defaultValue="QA_PERSONNEL" required>
        <option value="QA_PERSONNEL">QA Personnel (Complaint Triage &amp; RCA)</option>
        <option value="PRODUCTION_PERSONNEL">Production Personnel (Batches &amp; BMR)</option>
        <option value="QC_PERSONNEL">QC Personnel (Analytical Testing)</option>
      </select>
    </label>
  </>
);

export default EmployeeRegisterFields;
