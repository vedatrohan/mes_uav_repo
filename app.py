import math
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st

st.set_page_config(
    page_title="Bilkent UAV Sizing Suite", layout="wide", page_icon="✈️"
)

st.title("🛩️ Bilkent UAV Conceptual Sizing & Aero Synthesis")
st.caption(
    "Interactive aircraft sizing loop: Mission Limits → Constraint Analysis → Wing & Taper → Tail → Drag Buildup."
)

# -------------------------------------------------------------
# STAGE 1: MISSION PROFILE & DESIGN LIMITS
# -------------------------------------------------------------
st.header("1. Mission Profiles & Operational Constraints")

with st.expander("⚙️ Mission Settings & Flight Phases", expanded=True):
    col_p1, col_p2, col_p3 = st.columns(3)

    with col_p1:
        st.subheader("Takeoff & Surface")
        target_mtow = st.number_input("Target MTOW (kg)", value=3.0, step=0.1)
        v_to = st.number_input("Takeoff Speed V_TO (m/s)", value=11.0, step=0.5)
        s_g = st.number_input("Ground Run Limit S_g (m)", value=20.0, step=1.0)
        runway_friction = st.selectbox(
            "Runway Type / Friction (μ)",
            options=[0.04, 0.08, 0.12, 0.20],
            index=1,
            format_func=lambda x: {
                0.04: "Asphalt / Concrete (μ = 0.04)",
                0.08: "Smooth / Cut Turf (μ = 0.08)",
                0.12: "Standard Grass (μ = 0.12)",
                0.20: "Tall Grass / Soft Field (μ = 0.20)",
            }[x],
        )

    with col_p2:
        st.subheader("Cruise & Maneuver")
        v_cruise = st.number_input("Cruise Speed (m/s)", value=16.0, step=0.5)
        v_climb = st.number_input("Climb Speed (m/s)", value=13.0, step=0.5)
        climb_rate = st.number_input(
            "Rate of Climb V_v (m/s)", value=3.0, step=0.5
        )
        n_turn = st.slider(
            "Sustained Turn Load Factor n", 1.0, 2.5, 1.41, step=0.05
        )

    with col_p3:
        st.subheader("Approach, Stall & Atmosphere")
        v_stall = st.number_input(
            "Max Stall Speed on Approach (m/s)", value=10.0, step=0.5
        )
        cl_max_est = st.number_input(
            "Estimated CL_max (Clean/Approach)", value=1.4, step=0.05
        )
        rho = st.number_input(
            "Air Density ρ (kg/m³)", value=1.225, format="%.3f"
        )
        cd0_active = st.number_input(
            "Parasite Drag CD0 (Estimated)", value=0.035, format="%.4f"
        )

# -------------------------------------------------------------
# STAGE 2: CONSTRAINT ANALYSIS ENGINE
# -------------------------------------------------------------
st.header("2. Master Constraint Analysis")

col_ar, col_plot = st.columns([1, 2])

with col_ar:
    ar = st.slider("Wing Aspect Ratio (AR)", 4.0, 12.0, 7.0, step=0.5)
    e0 = 1.14 - 0.0801 * (ar**0.68)
    k_factor = 1.0 / (math.pi * ar * e0)
    st.caption(
        f"**Oswald Efficiency ($e_0$):** {e0:.3f}\n\n**Induced Drag Factor ($k$):** {k_factor:.4f}"
    )

    # Dynamic pressures
    g = 9.80665
    q_cruise = 0.5 * rho * (v_cruise**2)
    q_climb = 0.5 * rho * (v_climb**2)
    q_to_avg = 0.5 * rho * ((v_to / math.sqrt(2)) ** 2)
    q_stall = 0.5 * rho * (v_stall**2)

    # Stall point calculation
    ws_crit_pa = q_stall * cl_max_est
    opt_ws_kgm2 = ws_crit_pa / g

    # Critical T/W evaluations at stall wing loading
    tw_crit = {
        "Cruise": (q_cruise * cd0_active / ws_crit_pa)
        + (k_factor * ws_crit_pa / q_cruise),
        "Climb": (climb_rate / v_climb)
        + (q_climb * cd0_active / ws_crit_pa)
        + (k_factor * ws_crit_pa / q_climb),
        "Turn": q_cruise
        * (
            (cd0_active / ws_crit_pa)
            + ws_crit_pa * k_factor * ((n_turn / q_cruise) ** 2)
        ),
        "Takeoff Ground Run": (v_to**2) / (2 * g * s_g)
        + (q_to_avg * 0.05 / ws_crit_pa)
        + runway_friction * (1.0 - (q_to_avg * 0.6 / ws_crit_pa)),
    }
    governing_phase = max(tw_crit, key=tw_crit.get)
    opt_tw = tw_crit[governing_phase]

    s_req = target_mtow / opt_ws_kgm2
    req_thrust_kgf = target_mtow * opt_tw

    st.success(f"**Governing Phase:** {governing_phase}")
    st.metric("Optimal Wing Loading (W/S)", f"{opt_ws_kgm2:.2f} kg/m²")
    st.metric("Minimum Thrust-to-Weight (T/W)", f"{opt_tw:.2f}")
    st.metric("Required Wing Area (S)", f"{s_req:.3f} m²")
    st.metric("Static Thrust Target", f"{req_thrust_kgf:.2f} kgf")

with col_plot:
    ws_pa_range = np.linspace(1.0, 16.0 * g, 300)
    ws_kgm2_range = ws_pa_range / g

    tw_c_plot = (q_cruise * cd0_active / ws_pa_range) + (
        k_factor * ws_pa_range / q_cruise
    )
    tw_cl_plot = (
        (climb_rate / v_climb)
        + (q_climb * cd0_active / ws_pa_range)
        + (k_factor * ws_pa_range / q_climb)
    )
    tw_t_plot = q_cruise * (
        (cd0_active / ws_pa_range)
        + ws_pa_range * k_factor * ((n_turn / q_cruise) ** 2)
    )
    tw_to_plot = (
        (v_to**2) / (2 * g * s_g)
        + (q_to_avg * 0.05 / ws_pa_range)
        + runway_friction * (1.0 - (q_to_avg * 0.6 / ws_pa_range))
    )

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=100)
    ax.plot(ws_kgm2_range, tw_c_plot, label="Cruise", color="crimson")
    ax.plot(ws_kgm2_range, tw_cl_plot, label="Climb", color="royalblue")
    ax.plot(ws_kgm2_range, tw_t_plot, label="Turn", color="purple")
    ax.plot(ws_kgm2_range, tw_to_plot, label="Takeoff", color="forestgreen")
    ax.axvline(
        x=opt_ws_kgm2,
        label="Stall Limit",
        color="black",
        linestyle="--",
        lw=1.5,
    )
    ax.plot(
        opt_ws_kgm2,
        opt_tw,
        "ro",
        markersize=8,
        label=f"Design Point ({opt_ws_kgm2:.2f}, {opt_tw:.2f})",
    )

    upper_bounds = np.maximum.reduce(
        [tw_c_plot, tw_cl_plot, tw_t_plot, tw_to_plot]
    )
    feasible_mask = ws_kgm2_range <= opt_ws_kgm2
    ax.fill_between(
        ws_kgm2_range[feasible_mask],
        upper_bounds[feasible_mask],
        1.2,
        color="lightgray",
        alpha=0.4,
    )

    ax.set_xlim(0, 16.0)
    ax.set_ylim(0, 1.2)
    ax.set_xlabel("Wing Loading W/S (kg/m²)")
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
    apply_taper = st.checkbox(
        "Apply Wing Taper (λ < 1.0)?",
        value=False,
        help="Simplicity priority or short flight time suggests rectangular wing (no taper).",
    )

    if not apply_taper:
        lambda_val = 1.0
        c_root = s_req / wingspan_total
        c_tip = c_root
        mac = c_root
        st.info("Using Rectangular Wing (Simple build, hot-wire friendly).")
    else:
        lambda_val = st.slider(
            "Taper Ratio λ (c_tip / c_root)", 0.5, 0.8, 0.65, step=0.05
        )
        c_root = (2.0 / (1.0 + lambda_val)) * math.sqrt(s_req / ar)
        c_tip = lambda_val * c_root
        mac = (
            (2.0 / 3.0)
            * c_root
            * ((1.0 + lambda_val + lambda_val**2) / (1.0 + lambda_val))
        )

    # Reynolds number evaluation
    nu_air = 1.48e-5
    re_root = (v_cruise * c_root) / nu_air
    re_tip_stall = (v_stall * c_tip) / nu_air

with col_w2:
    st.write("**Calculated Wing Metrics:**")
    m_col1, m_col2 = st.columns(2)
    m_col1.metric("Wingspan (b)", f"{wingspan_total:.2f} m")
    m_col1.metric("Root Chord (c_root)", f"{c_root*100:.1f} cm")
    m_col2.metric("Mean Aero Chord (MAC)", f"{mac*100:.1f} cm")
    m_col2.metric("Tip Chord (c_tip)", f"{c_tip*100:.1f} cm")

    st.write(
        f"• **Cruise Re (Root):** {re_root:,.0f}\n\n• **Stall Re (Tip):** {re_tip_stall:,.0f}"
    )

    if apply_taper and re_tip_stall < 100000:
        st.error(
            "⚠️ **Tip Stall Danger:** Tip Reynolds number at stall is below 100,000. "
            "Flow will detach at wingtips first, causing violent roll stalls. "
            "Increase tip chord or incorporate -1.5° to -2.0° negative washout twist."
        )

# -------------------------------------------------------------
# STAGE 4: AIRFOIL SELECTION CHECKPOINT (3D TO 2D POLARS)
# -------------------------------------------------------------
st.header("4. Airfoil 2D Lift Translation")

with st.expander("📊 3D to 2D Airfoil Coefficients", expanded=False):
    st.caption(
        "Convert 3D wing requirements into target 2D sectional lift (Cl) to search on AirfoilTools."
    )
    col_af1, col_af2 = st.columns(2)

    with col_af1:
        # 3D cruise lift coefficient required: L = W
        cl_3d_cruise = (2.0 * target_mtow * g) / (
            rho * (v_cruise**2) * s_req
        )
        cl_2d_cruise = cl_3d_cruise / (
            1.0 - (cl_3d_cruise / (math.pi * ar * e0))
        )
        cl_2d_stall = cl_max_est / (
            1.0 - (cl_max_est / (math.pi * ar * e0))
        )

        st.metric("Target 3D Wing CL (Cruise)", f"{cl_3d_cruise:.3f}")
        st.metric("Target 2D Airfoil Cl (Cruise)", f"{cl_2d_cruise:.3f}")
        st.metric("Target 2D Airfoil Cl_max", f"{cl_2d_stall:.3f}")

    with col_af2:
        st.markdown(
            """
        **Profile Selection Recommendations:**
        * Thickness range: $10\% \le t/c \le 14\%$ (for structural spar depth).
        * Camber: $2\% \le c_m \le 4\%$ (Clark Y, Selig S1223, or NACA 2412).
        * Look up polars around: $Re \\approx$ **{re:,.0f}**.
        """.format(
                re=re_root
            )
        )

# -------------------------------------------------------------
# STAGE 5: TAIL SIZING (VOLUME COEFFICIENTS)
# -------------------------------------------------------------
st.header("5. Empennage / Tail Surface Sizing")

with st.expander("📐 Tail Moments & Areas", expanded=True):
    col_tl1, col_tl2 = st.columns(2)

    with col_tl1:
        st.subheader("Horizontal Tail (Pitch Stability)")
        l_h = st.slider(
            "Horizontal Moment Arm l_H (m)", 0.40, 1.20, 0.65, step=0.05
        )
        v_h = st.slider(
            "Volume Coefficient V_H",
            0.40,
            0.85,
            0.60,
            step=0.05,
            help="Typical values: 0.50 - 0.70 for trainers/gliders",
        )
        s_h = (v_h * s_req * mac) / l_h
        st.metric("Horizontal Tail Area (S_H)", f"{s_h*10000:.1f} cm²")

    with col_tl2:
        st.subheader("Vertical Tail (Yaw Stability)")
        l_v = st.slider(
            "Vertical Moment Arm l_V (m)", 0.40, 1.20, 0.65, step=0.05
        )
        v_v = st.slider(
            "Volume Coefficient V_V",
            0.02,
            0.06,
            0.04,
            step=0.005,
            help="Typical values: 0.035 - 0.050",
        )
        s_v = (v_v * s_req * wingspan_total) / l_v
        st.metric("Vertical Fin Area (S_V)", f"{s_v*10000:.1f} cm²")

# -------------------------------------------------------------
# STAGE 6: DRAG BUILDUP FEEDBACK LOOP
# -------------------------------------------------------------
st.header("6. Component Drag Buildup & Loop Verification")

with st.expander("🛠️ Fuselage & Gear Drag Assessment", expanded=False):
    col_dg1, col_dg2 = st.columns(2)

    with col_dg1:
        fuse_len = st.number_input("Fuselage Length (m)", value=0.90, step=0.05)
        fuse_dia = st.number_input(
            "Fuselage Equivalent Diameter (m)", value=0.12, step=0.01
        )
        gear_type = st.selectbox(
            "Landing Gear Configuration",
            [
                "No Gear (Bungee/Hand Launch) - 0.0",
                "Round Wire Strut & Clean Wheels - 0.30",
                "Flat Spring Aluminum Legs - 1.40",
            ],
            index=1,
        )

        fineness = fuse_len / fuse_dia
        form_factor = 1.0 + (60.0 / (fineness**3)) + (fineness / 400.0)

    with col_dg2:
        st.write(f"• **Fineness Ratio ($f$):** {fineness:.2f}")
        st.write(f"• **Fuselage Form Factor ($FF$):** {form_factor:.3f}")

        recalc_cd0 = st.number_input(
            "Updated Total CD0 from Buildup", value=0.0360, format="%.4f"
        )

        if abs(recalc_cd0 - cd0_active) > 0.003:
            st.warning(
                f"⚠️ Iteration Discrepancy! Your re-calculated parasite drag ({recalc_cd0:.4f}) "
                f"deviates from the assumption in Step 1 ({cd0_active:.4f}). "
                "Update Step 1 Parasite Drag to re-close the synthesis loop."
            )
        else:
            st.success("✅ Parasite drag assumption matches component buildup.")