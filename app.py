import math
import json
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st

st.set_page_config(page_title="Bilkent UAV Sizing Suite", layout="wide")

# -------------------------------------------------------------
# STATE MANAGEMENT & CONFIGURATION I/O
# -------------------------------------------------------------
default_config = {
    "target_mtow": 3.0,
    "v_to": 11.0,
    "s_g": 20.0,
    "runway_friction_idx": 1,
    "v_cruise": 16.0,
    "v_climb": 13.0,
    "climb_rate": 3.0,
    "n_turn": 1.41,
    "v_stall": 10.0,
    "cl_max_est": 1.4,
    "rho": 1.225,
    "g": 9.80665,
    "cd0_active": 0.035,
    "max_wind": 5.0,
    "wind_sf": 1.2
}

for key, val in default_config.items():
    if key not in st.session_state:
        st.session_state[key] = val

st.title("Bilkent UAV Conceptual Sizing & Aero Synthesis")
st.caption("Interactive aircraft sizing loop: Mission Limits -> Constraint Analysis -> Wing & Taper -> Tail -> Drag Buildup.")

with st.sidebar:
    st.header("Configuration Management")
    
    config_export = {k: st.session_state[k] for k in default_config.keys()}
    st.download_button(
        label="Download Mission Settings (JSON)",
        data=json.dumps(config_export, indent=4),
        file_name="uav_mission_config.json",
        mime="application/json"
    )
    
    st.markdown("---")
    uploaded_file = st.file_uploader("Upload Settings (JSON)", type=["json"])
    if uploaded_file is not None:
        if st.button("Apply Uploaded Config"):
            try:
                new_config = json.load(uploaded_file)
                for k in default_config.keys():
                    if k in new_config:
                        st.session_state[k] = new_config[k]
                st.rerun()
            except Exception:
                st.error("Invalid JSON format.")

def update_cd0_callback():
    st.session_state.cd0_active = st.session_state.temp_recalc_cd0
# -------------------------------------------------------------
# STAGE 1: MISSION PROFILE & DESIGN LIMITS
# -------------------------------------------------------------
st.header("1. Mission Profiles & Operational Constraints")

col_p1, col_p2, col_p3 = st.columns(3)

with col_p1:
    st.subheader("Takeoff & Surface")
    target_mtow = st.number_input("Target MTOW (kg)", step=0.1, key="target_mtow")
    v_to = st.number_input("Takeoff Speed V_TO (m/s)", step=0.5, key="v_to")
    s_g = st.number_input("Ground Run Limit S_g (m)", step=1.0, key="s_g")
    
    friction_options = [0.04, 0.08, 0.12, 0.20]
    st.selectbox(
        "Runway Type / Friction (mu)",
        options=range(len(friction_options)),
        format_func=lambda x: {
            0: "Asphalt / Concrete (mu = 0.04)",
            1: "Smooth / Cut Turf (mu = 0.08)",
            2: "Standard Grass (mu = 0.12)",
            3: "Tall Grass / Soft Field (mu = 0.20)",
        }[x],
        key="runway_friction_idx"
    )
    runway_friction = friction_options[st.session_state.runway_friction_idx]

with col_p2:
    st.subheader("Cruise, Climb & Wind")
    v_cruise = st.number_input("Cruise Speed (m/s)", step=0.5, key="v_cruise")
    v_climb = st.number_input("Climb Speed (m/s)", step=0.5, key="v_climb")
    climb_rate = st.number_input("Rate of Climb V_v (m/s)", step=0.5, key="climb_rate")
    n_turn = st.slider("Sustained Turn Load Factor n", 1.0, 2.5, step=0.05, key="n_turn")
    st.markdown("---")
    max_wind = st.number_input("Max Gust / Wind Speed (m/s)", step=0.5, key="max_wind")
    wind_sf = st.number_input("Wind Safety Factor", step=0.1, key="wind_sf")

with col_p3:
    st.subheader("Approach & Environment")
    v_stall = st.number_input("Max Stall Speed on Approach (m/s)", step=0.5, key="v_stall")
    cl_max_est = st.number_input("Estimated CL_max (Clean/Approach)", step=0.05, key="cl_max_est")
    rho = st.number_input("Air Density rho (kg/m^3)", format="%.3f", key="rho")
    g = st.number_input("Gravity g (m/s^2)", format="%.5f", key="g")
    cd0_active = st.number_input("Parasite Drag CD0 (Estimated)", format="%.4f", key="cd0_active")

# Wind Penetration Check
wind_margin = v_cruise - (max_wind * wind_sf)
if wind_margin <= v_stall:
    st.error(f"Wind Penetration Warning: Cruise speed ({v_cruise} m/s) minus factored wind ({max_wind * wind_sf:.1f} m/s) is {wind_margin:.1f} m/s. This is below or equal to the stall speed ({v_stall} m/s). The aircraft risks stalling or flying backward relative to the ground during gusts.")
else:
    st.success(f"Wind Penetration Pass: Factored margin ({wind_margin:.1f} m/s) clears stall speed.")

# -------------------------------------------------------------
# STAGE 2: CONSTRAINT ANALYSIS ENGINE
# -------------------------------------------------------------
st.header("2. Master Constraint Analysis")

col_ar, col_plot = st.columns([1, 2])

with col_ar:
    ar = st.slider("Wing Aspect Ratio (AR)", 4.0, 12.0, 7.0, step=0.5)
    e0 = 1.14 - 0.0801 * (ar**0.68)
    k_factor = 1.0 / (math.pi * ar * e0)
    st.caption(f"Oswald Efficiency (e0): {e0:.3f} | Induced Drag Factor (k): {k_factor:.4f}")
    
    st.markdown("---")
    max_ws_plot = st.number_input("Plot Max W/S (kg/m^2)", value=16.0, step=1.0)
    max_tw_plot = st.number_input("Plot Max T/W", value=1.2, step=0.1)

    q_cruise = 0.5 * rho * (v_cruise**2)
    q_climb = 0.5 * rho * (v_climb**2)
    q_to_avg = 0.5 * rho * ((v_to / math.sqrt(2)) ** 2)
    q_stall = 0.5 * rho * (v_stall**2)

    ws_crit_pa = q_stall * cl_max_est
    opt_ws_kgm2 = ws_crit_pa / g

    tw_crit = {
        "Cruise": (q_cruise * cd0_active / ws_crit_pa) + (k_factor * ws_crit_pa / q_cruise),
        "Climb": (climb_rate / v_climb) + (q_climb * cd0_active / ws_crit_pa) + (k_factor * ws_crit_pa / q_climb),
        "Turn": q_cruise * ((cd0_active / ws_crit_pa) + ws_crit_pa * k_factor * ((n_turn / q_cruise) ** 2)),
        "Takeoff Ground Run": (v_to**2) / (2 * g * s_g) + (q_to_avg * 0.05 / ws_crit_pa) + runway_friction * (1.0 - (q_to_avg * 0.6 / ws_crit_pa)),
    }
    
    governing_phase = max(tw_crit, key=tw_crit.get)
    opt_tw = tw_crit[governing_phase]
    s_req = target_mtow / opt_ws_kgm2
    req_thrust_kgf = target_mtow * opt_tw

    st.metric("Optimal Wing Loading (W/S)", f"{opt_ws_kgm2:.2f} kg/m^2")
    st.metric("Minimum Thrust-to-Weight (T/W)", f"{opt_tw:.2f}")
    st.metric("Required Wing Area (S)", f"{s_req:.3f} m^2")
    st.metric("Static Thrust Target", f"{req_thrust_kgf:.2f} kgf")

with col_plot:
    ws_pa_range = np.linspace(1.0, max_ws_plot * g, 300)
    ws_kgm2_range = ws_pa_range / g

    tw_c_plot = (q_cruise * cd0_active / ws_pa_range) + (k_factor * ws_pa_range / q_cruise)
    tw_cl_plot = ((climb_rate / v_climb) + (q_climb * cd0_active / ws_pa_range) + (k_factor * ws_pa_range / q_climb))
    tw_t_plot = q_cruise * ((cd0_active / ws_pa_range) + ws_pa_range * k_factor * ((n_turn / q_cruise) ** 2))
    tw_to_plot = ((v_to**2) / (2 * g * s_g) + (q_to_avg * 0.05 / ws_pa_range) + runway_friction * (1.0 - (q_to_avg * 0.6 / ws_pa_range)))

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=100)
    ax.plot(ws_kgm2_range, tw_c_plot, label="Cruise", color="crimson")
    ax.plot(ws_kgm2_range, tw_cl_plot, label="Climb", color="royalblue")
    ax.plot(ws_kgm2_range, tw_t_plot, label="Turn", color="purple")
    ax.plot(ws_kgm2_range, tw_to_plot, label="Takeoff", color="forestgreen")
    ax.axvline(x=opt_ws_kgm2, label="Stall Limit", color="black", linestyle="--", lw=1.5)
    ax.plot(opt_ws_kgm2, opt_tw, "ro", markersize=8, label=f"Design Point ({opt_ws_kgm2:.2f}, {opt_tw:.2f})")

    upper_bounds = np.maximum.reduce([tw_c_plot, tw_cl_plot, tw_t_plot, tw_to_plot])
    feasible_mask = ws_kgm2_range <= opt_ws_kgm2
    ax.fill_between(ws_kgm2_range[feasible_mask], upper_bounds[feasible_mask], max_tw_plot, color="lightgray", alpha=0.4)

    ax.set_xlim(0, max_ws_plot)
    ax.set_ylim(0, max_tw_plot)
    ax.set_xlabel("Wing Loading W/S (kg/m^2)")
    ax.set_ylabel("Thrust-to-Weight Ratio T/W")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="upper right", fontsize=8.5)
    st.pyplot(fig)

# -------------------------------------------------------------
# STAGE 3: WING SIZING & TAPER CHECKPOINT
# -------------------------------------------------------------
st.header("3. Wing Geometry & Taper Selection")
wingspan_total = math.sqrt(ar * s_req)

col_w1, col_w2 = st.columns([1, 1])

with col_w1:
    apply_taper = st.checkbox("Apply Wing Taper (lambda < 1.0)?", value=False)
    nu_air = st.number_input("Kinematic Viscosity of Air (m^2/s)", value=1.48e-5, format="%.2e")
    re_crit_tip = st.slider("Critical Tip Stall Reynolds Number", 50000, 200000, 100000, step=10000)

    if not apply_taper:
        lambda_val = 1.0
        c_root = s_req / wingspan_total
        c_tip = c_root
        mac = c_root
        st.info("Using Rectangular Wing.")
    else:
        lambda_val = st.slider("Taper Ratio lambda (c_tip / c_root)", 0.2, 1.0, 0.65, step=0.05)
        c_root = (2.0 / (1.0 + lambda_val)) * math.sqrt(s_req / ar)
        c_tip = lambda_val * c_root
        mac = (2.0 / 3.0) * c_root * ((1.0 + lambda_val + lambda_val**2) / (1.0 + lambda_val))

    re_root = (v_cruise * c_root) / nu_air
    re_tip_stall = (v_stall * c_tip) / nu_air

with col_w2:
    st.write("Calculated Wing Metrics:")
    m_col1, m_col2 = st.columns(2)
    m_col1.metric("Wingspan (b)", f"{wingspan_total:.2f} m")
    m_col1.metric("Root Chord (c_root)", f"{c_root*100:.1f} cm")
    m_col2.metric("Mean Aero Chord (MAC)", f"{mac*100:.1f} cm")
    m_col2.metric("Tip Chord (c_tip)", f"{c_tip*100:.1f} cm")

    st.write(f"Cruise Re (Root): {re_root:,.0f} | Stall Re (Tip): {re_tip_stall:,.0f}")

    if apply_taper and re_tip_stall < re_crit_tip:
        st.error(f"Tip Stall Danger: Tip Reynolds number at stall ({re_tip_stall:,.0f}) is below the critical threshold of {re_crit_tip}. Flow will detach at wingtips first. Increase tip chord or incorporate negative washout twist.")

# -------------------------------------------------------------
# STAGE 4: AIRFOIL SELECTION CHECKPOINT
# -------------------------------------------------------------
st.header("4. Airfoil 2D Lift Translation")

cl_3d_cruise = (2.0 * target_mtow * g) / (rho * (v_cruise**2) * s_req)
cl_2d_cruise = cl_3d_cruise / (1.0 - (cl_3d_cruise / (math.pi * ar * e0)))
cl_2d_stall = cl_max_est / (1.0 - (cl_max_est / (math.pi * ar * e0)))

col_af1, col_af2, col_af3 = st.columns(3)
col_af1.metric("Target 3D Wing CL (Cruise)", f"{cl_3d_cruise:.3f}")
col_af2.metric("Target 2D Airfoil Cl (Cruise)", f"{cl_2d_cruise:.3f}")
col_af3.metric("Target 2D Airfoil Cl_max", f"{cl_2d_stall:.3f}")

# -------------------------------------------------------------
# STAGE 5: TAIL SIZING (VOLUME COEFFICIENTS)
# -------------------------------------------------------------
st.header("5. Empennage / Tail Surface Sizing")

col_tl1, col_tl2 = st.columns(2)
with col_tl1:
    st.subheader("Horizontal Tail")
    l_h = st.slider("Horizontal Moment Arm l_H (m)", 0.40, 1.20, 0.65, step=0.05)
    v_h = st.slider("Volume Coefficient V_H", 0.40, 0.85, 0.60, step=0.05)
    ar_h = st.slider("Horiz. Tail AR", 3.0, 6.0, 4.0, step=0.1)
    lam_h = st.slider("Horiz. Tail Taper lambda_h", 0.3, 1.0, 0.8, step=0.05)
    
    s_h = (v_h * s_req * mac) / l_h
    b_h = math.sqrt(ar_h * s_h)
    c_root_h = (2.0 * s_h) / (b_h * (1.0 + lam_h))
    
    st.metric("Horizontal Tail Area (S_H)", f"{s_h:.3f} m^2")
    st.write(f"Span (b_h): {b_h:.2f} m | Root Chord (cr_h): {c_root_h*100:.1f} cm | Tip Chord: {lam_h*c_root_h*100:.1f} cm")

with col_tl2:
    st.subheader("Vertical Tail")
    l_v = st.slider("Vertical Moment Arm l_V (m)", 0.40, 1.20, 0.65, step=0.05)
    v_v = st.slider("Volume Coefficient V_V", 0.02, 0.06, 0.04, step=0.005)
    ar_v = st.slider("Vert. Tail AR", 1.0, 3.0, 1.5, step=0.1)
    lam_v = st.slider("Vert. Tail Taper lambda_v", 0.3, 1.0, 0.6, step=0.05)
    
    s_v = (v_v * s_req * wingspan_total) / l_v
    h_v = math.sqrt(ar_v * s_v)
    c_root_v = (2.0 * s_v) / (h_v * (1.0 + lam_v))
    
    st.metric("Vertical Fin Area (S_V)", f"{s_v:.3f} m^2")
    st.write(f"Height (h_v): {h_v:.2f} m | Root Chord (cr_v): {c_root_v*100:.1f} cm | Tip Chord: {lam_v*c_root_v*100:.1f} cm")

# -------------------------------------------------------------
# STAGE 6: DRAG BUILDUP FEEDBACK LOOP
# -------------------------------------------------------------
st.header("6. Component Drag Buildup & Loop Verification")

col_dg1, col_dg2 = st.columns(2)

with col_dg1:
    fuse_len = st.number_input("Fuselage Length (m)", value=0.90, step=0.05)
    fuse_dia = st.number_input("Fuselage Equivalent Diameter (m)", value=0.12, step=0.01)
    s_wet_fuse = st.number_input("Fuselage Wetted Area S_wet (m^2)", value=(math.pi * fuse_dia * fuse_len * 0.8), format="%.3f")
    cf_fuse = st.number_input("Fuselage Skin Friction Coefficient (Cf)", value=0.005, format="%.4f")
    cd_wing_emp = st.number_input("Wing & Empennage Base Profile Drag", value=0.012, format="%.4f")

    gear_cd_added = st.selectbox(
        "Landing Gear Configuration (Added CD_gear)",
        options=[0.000, 0.008, 0.015],
        index=1,
        format_func=lambda x: {
            0.000: "No Gear / Retracts (dCD = 0.000)",
            0.008: "Streamlined Gear / Pants (dCD = 0.008)",
            0.015: "Bare Wire/Spring & Wheels (dCD = 0.015)"
        }[x]
    )

    fineness = fuse_len / fuse_dia
    form_factor = 1.0 + (60.0 / (fineness**3)) + (fineness / 400.0)
    cd0_fuse = (cf_fuse * form_factor * s_wet_fuse) / s_req
    total_recalc_cd0 = cd0_fuse + cd_wing_emp + gear_cd_added

with col_dg2:
    st.write(f"Fineness Ratio (f): {fineness:.2f}")
    st.write(f"Fuselage Form Factor (FF): {form_factor:.3f}")
    st.write("---")
    st.write(f"Calculated Fuselage CD0: {cd0_fuse:.4f}")
    st.write(f"Wing/Empennage CD0: {cd_wing_emp:.4f}")
    st.write(f"Landing Gear CD0: {gear_cd_added:.4f}")
    
    st.metric("Re-calculated Total CD0", f"{total_recalc_cd0:.4f}")

    if abs(total_recalc_cd0 - st.session_state.cd0_active) > 0.003:
        st.warning(f"Iteration Discrepancy: Your re-calculated parasite drag ({total_recalc_cd0:.4f}) deviates from the assumption in Stage 1 ({st.session_state.cd0_active:.4f}).")
    else:
        st.success("Parasite drag assumption matches component buildup.")

st.session_state.temp_recalc_cd0 = float(total_recalc_cd0)

st.markdown("---")
st.button(
    "Update Stage 1 Drag Assumption and Recalculate",
    on_click=update_cd0_callback
)
