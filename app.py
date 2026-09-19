# -------------------------------------------------------------
# STAGE 3: WING SIZING & CUSTOM STATIC MARGIN CHECK
# -------------------------------------------------------------
st.header("3. Wing Sizing & Longitudinal Stability")
wingspan_total = math.sqrt(ar * s_req)

col_w1, col_w2 = st.columns(2)

with col_w1:
    apply_taper = st.checkbox("Apply Wing Taper (lambda < 1.0)?", value=False)
    if not apply_taper:
        lambda_val = 1.0
        c_root = s_req / wingspan_total
        c_tip = c_root
        mac = c_root
    else:
        lambda_val = st.slider("Taper Ratio lambda (c_tip / c_root)", 0.2, 1.0, 0.65, step=0.05)
        c_root = (2.0 / (1.0 + lambda_val)) * math.sqrt(s_req / ar)
        c_tip = lambda_val * c_root
        mac = (2.0 / 3.0) * c_root * ((1.0 + lambda_val + lambda_val**2) / (1.0 + lambda_val))

    x_le_wing = st.number_input("Wing Leading Edge Location from Nose X_LE (mm)", step=10.0, key="x_le_wing")
    x_ac_wing = x_le_wing + (0.25 * mac * 1000.0)
    static_margin = ((x_ac_wing - x_cg_mm) / (mac * 1000.0)) * 100.0
    
    st.markdown("---")
    re_tip_threshold = st.slider("Tip Reynolds Warning Threshold", min_value=50000, max_value=300000, value=100000, step=10000)

with col_w2:
    st.write("Calculated Metrics:")
    sm1, sm2 = st.columns(2)
    sm1.metric("Wingspan (b)", f"{wingspan_total:.2f} m")
    sm1.metric("Mean Aero Chord (MAC)", f"{mac * 100:.1f} cm")
    sm2.metric("Wing Aerodynamic Center", f"{x_ac_wing:.1f} mm")
    sm2.metric("Calculated Static Margin", f"{static_margin:.1f}% MAC")

    re_cruise = (rho * v_cruise * mac) / mu_air if mu_air > 0 else 0.0
    re_tip = (rho * v_cruise * c_tip) / mu_air if mu_air > 0 else 0.0
    
    st.caption(f"Cruise Reynolds Number (based on MAC): **Re = {re_cruise:,.0f}**")
    st.caption(f"Tip Reynolds Number: **Re_tip = {re_tip:,.0f}**")

    if re_tip < re_tip_threshold:
        st.warning(f"Flow Separation Risk: Tip Re ({re_tip:,.0f}) is below the {re_tip_threshold:,} threshold. Consider increasing tip chord or cruise speed.")

    if static_margin < sm_min:
        st.error(f"Stability Hazard: Static margin is {static_margin:.1f}%, below set threshold of {sm_min:.1f}%. Move mass forward or wing aft.")
    elif static_margin > sm_max:
        st.warning(f"Over-Stable / High Trim Drag: Static margin is {static_margin:.1f}%, above set threshold of {sm_max:.1f}%. Move mass aft or wing forward.")
    else:
        st.success(f"Stability Acceptable: Static margin is inside bounds ({sm_min:.1f}% - {sm_max:.1f}%).")
