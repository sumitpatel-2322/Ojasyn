import React from "react";

const CustomerRegisterFields = ({ isChemist, setIsChemist }) => (
  <>
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
        autoComplete="tel"
        inputMode="tel"
        pattern="[0-9+() -]{8,16}"
        required
      />
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
      <input type="text" name="pincode" placeholder="Enter 6-digit PIN code" inputMode="numeric" pattern="[0-9]{6}" required />
    </label>
    <label className="chemist-toggle">
      <input
        type="checkbox"
        name="is_chemist"
        checked={isChemist}
        onChange={(event) => setIsChemist(event.target.checked)}
      />{" "}
      I am registering as a Chemist / Pharmacy
    </label>
    {isChemist && (
      <div className="chemist-fields">
        <p className="chemist-fields-title">Chemist / Pharmacy Profile</p>
        <label className="auth-field">
          Chemist / Pharmacy Name
          <input type="text" name="chemist_pharmacy_name" placeholder="Enter pharmacy name" required />
        </label>
        <label className="auth-field">
          Pharmacy Type
          <select name="pharmacy_type" defaultValue="Retail" required>
            <option value="Retail">Retail Pharmacy</option>
            <option value="Wholesale">Wholesale Distributor</option>
            <option value="Hospital">Hospital / Institutional Pharmacy</option>
          </select>
        </label>
        <label className="auth-field">
          Drug Licence Number
          <input type="text" name="drug_licence_number" placeholder="Enter drug licence number" required />
        </label>
        <label className="auth-field">
          GST Number (optional)
          <input type="text" name="gst_number" placeholder="Enter GST number" />
        </label>
        <label className="auth-field">
          Pharmacy Address
          <textarea className="chemist-address" name="pharmacy_address" placeholder="Enter complete pharmacy address" required />
        </label>
      </div>
    )}
  </>
);

export default CustomerRegisterFields;
