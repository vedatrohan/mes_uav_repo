import math
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Bilkent UAV Sizing Suite", layout="wide")

# -------------------------------------------------------------
# GOOGLE DRIVE / SHEETS MASS BUILDUP INGESTION
# -------------------------------------------------------------
st.sidebar.header("Mass Buildup (Google Drive)")

SHEET_ID = "1ksGFylLwYRefZ5e4smVkHqotms8hJYrnHfDF-Msib30"
SHEET_GID = "2084851165"

@st.cache_data(ttl=60)
def load_and_clean_mass_table(sheet_id: str, gid: str):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    raw_df = pd.read_csv(url, header=None)
    
    header_idx = None
    for idx, row in raw_df.iterrows():
        row_vals = [str(x).strip().lower() for x in row.dropna().tolist()]
        if any("ürün" in v or "urun" in v or "kategori" in v for v in row_vals):
            header_idx = idx
            break
            
    if header_idx is None:
        raise ValueError("Tablo başlık satırı bulunamadı.")

    df = raw_df.iloc[header_idx + 1:].copy()
    df.columns = [str(c).strip() for c in raw_df.iloc[header_idx]]
    df = df.dropna(how="all", axis=1).dropna(how="all", axis=0)
    
    def normalize_str(s):
        return (
            str(s).strip().lower()
            .replace("ı", "i").replace("İ", "i")
            .replace("ğ", "g").replace("ü", "u")
            .replace("ş", "s").replace("ö", "o")
            .replace("ç", "c").replace(" ", "")
        )

    urun_col, weight_col, x_col, y_col, z_col = None, None, None, None, None

    for col in df.columns:
        norm = normalize_str(col)
        if any(k in norm for k in ["urun", "item", "part"]):
            urun_col = col
        elif any(k in norm for k in ["agirlik", "mass", "weight"]):
            weight_col = col
        elif norm.startswith("x"):
            x_col = col
        elif norm.startswith("y"):
            y_col = col
        elif norm.startswith("z"):
            z_col = col

    if not weight_col or not urun_col:
        raise KeyError(f"Zorunlu sütunlar bulunamadı. Mevcut sütunlar: {list(df.columns)}")

    df = df[df[urun_col].notna()]
    df = df[~df[urun_col].astype(str).str.contains("Toplam|Total", case=False)]

    for col in [weight_col, x_col, y_col, z_col]:
        if col and col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(".", "", regex=False)
                .str.replace(",", ".", regex=False)
                .str.extract(r"([-+]?\d*\.?\d+)", expand=False)
                .astype(float)
            )

    rename_map = {urun_col: "Ürün", weight_col: "Ağırlık(gr)"}
    if x_col: rename_map[x_col] = "X (mm)"
    if y_col: rename_map[y_col] = "Y (mm)"
    if z_col: rename_map[z_col] = "Z (mm)"
    df = df.rename(columns=rename_map)

    return df

live_mtow_kg = 3.0
x_cg_mm = 400.0
use_live_sheet = False

try:
    df_mass = load_and_clean_mass_table(SHEET_ID, SHEET_GID)
    total_mass_gr = df_mass["Ağırlık(gr)"].sum()
    live_mtow_kg = total_mass_gr / 1000.0
    
    total_moment_x = (df_mass["Ağırlık(gr)"] * df_mass["X (mm)"]).sum()
    x_cg_mm = total_moment_x / total_mass_gr
    
    st.sidebar.success("Mass table synced from Drive.")
    st.sidebar.metric("Live MTOW", f"{live_mtow_kg:.3f} kg")
    st.sidebar.metric("Longitudinal CG (from Nose)", f"{x_cg_mm:.1f} mm")
    use_live_sheet = True
except Exception as e:
    st.sidebar.warning(f"Could not load live sheet: {e}")
    st.sidebar.info("Falling back to manual weights.")

# -------------------------------------------------------------
# CONFIGURATION & STATE
# -------------------------------------------------------------
default_config = {
    "target_mtow": float(live_mtow_kg),
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
    "x_le_wing": 350.0,
    "eta_cruise": 0.72,
    "eta_climb": 0.65,
    "eta_to": 0.50,
    "eta_motor_esc": 0.82,
    "sm_min": 8.0,
    "sm_max": 15.0,
    "prop_dia_in": 12.0,
    "prop_pitch_in": 6.0,
    "prop_rpm": 7500.0,
    "prop_eta_max": 0.78,
    "use_dynamic_prop": True,
    "gust_velocity": 7.0,
    "n_pos_limit": 3.8
}

for key, val in default_config.items():
    if key not in st.session_state:
        st.session_state[key] = val

if use_live_sheet:
    st.session_state["target_mtow"] = float(live_mtow_kg)

st.title("Bilkent UAV Conceptual Sizing Suite (P/W Analysis)")
st.caption("Power-to-Weight Constraint Analysis with Dynamic Efficiencies, J Curves, Gust Checks, and CG Integration.")

with st.sidebar:
    st.header("Stability Margin Limits")
    sm_min = st.slider("Minimum Static Margin (% MAC)", 0.0, 15.0, step=0.5, key="sm_min")
    sm_max = st.slider("Maximum Static Margin (% MAC)", 15.0, 30.0, step=0.5, key="sm_max")

    st.markdown("---")
    st.header("Powertrain & Propeller Setup")
    use_dynamic_prop = st.checkbox("Model Propeller Efficiency via Advance Ratio (J)?", key="use_dynamic_prop")
    
    if use_dynamic_prop:
        prop_dia_in = st.number_input("Propeller Diameter (inches)", value=12.0, step=0.5, key="prop_dia_in")
        prop_pitch_in = st.number_input("Propeller Pitch (inches)", value=6.0, step=0.5, key="prop_pitch_in")
        prop_rpm = st.number_input("Operating Engine / Prop RPM", value=7500.0, step=250.0, key="prop_rpm")
        prop_eta_max = st.slider("Max Propeller Efficiency (eta_max)", 0.60, 0.88, 0.78, step=0.01, key="prop_eta_max")

        d_m = prop_dia_in * 0.0254
        p_m = prop_pitch_in * 0.0254
        n_rps = prop_rpm / 60.0
        j_pitch = p_m / d_m  # Advance ratio for zero thrust ~ pitch/dia

        def calc_eta_prop(v_mps):
            if n_rps <= 0 or d_m <= 0:
                return 0.5
            j = v_mps / (n_rps * d_m)
            # Normalized parabolic efficiency model centered at 0.75 * J_pitch
            j_opt = 0.75 * j_pitch
            if j <= 0:
                return 0.15 # Static thrust ground regime
            if j >= j_pitch:
                return 0.05
            eta = prop_eta_max * (1.0 - ((j - j_opt) / j_opt)**2)
            return max(float(eta), 0.15)

        eta_cruise = calc_eta_prop(st.session_state.v_cruise)
        eta_climb = calc_eta_prop(st.session_state.v_climb)
        eta_to = calc_eta_prop(st.session_state.v_to / math.sqrt(2))

        st.caption(f"Calculated Dynamic Efficiencies:")
        st.write(f"- Cruise J: `{st.session_state.v_cruise / (n_rps * d_m):.2f}` → $\\eta_p$: `{eta_cruise:.3f}`")
        st.write(f"- Climb J: `{st.session_state.v_climb / (n_rps * d_m):.2f}` → $\\eta_p$: `{eta_climb:.3f}`")
        st.write(f"- Takeoff J: `{(st.session_state.v_to/math.sqrt(2)) / (n_rps * d_m):.2f}` → $\\eta_p$: `{eta_to:.3f}`")
    else:
        eta_cruise = st.slider("Cruise Propeller Efficiency", 0.50, 0.85, step=0.01, key="eta_cruise")
        eta_climb = st.slider("Climb Propeller Efficiency", 0.45, 0.80, step=0.01, key="eta_climb")
        eta_to = st.slider("Takeoff Avg Propeller Efficiency", 0.35, 0.65, step=0.01, key="eta_to")

    eta_motor_esc = st.slider("Motor & ESC Efficiency", 0.70, 0.95, step=0.01, key="eta_motor_esc")

def update_cd0_callback():
    st.session_state.cd0_active = st.session_state.temp_recalc_cd0

# -------------------------------------------------------------
# STAGE 1: MISSION PROFILE
# -------------------------------------------------------------
st.header("1. Mission Profiles & Flight Parameters")
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
    st.subheader("Speeds & Maneuver")
    v_cruise = st.number_input("Cruise Speed (m/s)", step=0.5, key="v_cruise")
    v_climb = st.number_input("Climb Speed (m/s)", step=0.5, key="v_climb")
    climb_rate = st.number_input("Rate of Climb V_v (m/s)", step=0.5, key="climb_rate")
    n_turn = st.slider("Sustained Turn Load Factor n", 1.0, 2.5, step=0.05, key="n_turn")

with col_p3:
    st.subheader("Atmosphere & Aero Limits")
    v_stall = st.number_input("Stall Speed V_s (m/s)", step=0.5, key="v_stall")
    cl_max_est = st.number_input("Estimated CL_max", step=0.05, key="cl_max_est")
    rho = st.number_input("Density rho (kg/m^3)", format="%.3f", key="rho")
    g = st.number_input("Gravity g (m/s^2)", format="%.5f", key="g")
    cd0_active = st.number_input("Parasite Drag CD0", format="%.4f", key="cd0_active")

# -------------------------------------------------------------
# STAGE 2: P/W CONSTRAINT ANALYSIS
# -------------------------------------------------------------
st.header("2. Master Constraint Analysis (Power-to-Weight)")

col_ar, col_plot = st.columns([1, 2])

with col_ar:
    ar = st.slider("Aspect Ratio (AR)", 4.0, 12.0, 7.0, step=0.5)
    e0 = 1.14 - 0.0801 * (ar**0.68)
    k_factor = 1.0 / (math.pi * ar * e0)
    
    max_ws_plot = st.number_input("Plot Max W/S (kg/m^2)", value=16.0, step=1.0)
    max_pw_plot = st.number_input("Plot Max P/W (W/kg)", value=350.0, step=25.0)

    q_cruise = 0.5 * rho * (v_cruise**2)
    q_climb = 0.5 * rho * (v_climb**2)
    v_to_avg = v_to / math.sqrt(2)
    q_to_avg = 0.5 * rho * (v_to_avg**2)
    q_stall = 0.5 * rho * (v_stall**2)

    ws_crit_pa = q_stall * cl_max_est
    opt_ws_kgm2 = ws_crit_pa / g

    # Aerodynamic T/W requirements
    tw_cruise = (q_cruise * cd0_active / ws_crit_pa) + (k_factor * ws_crit_pa / q_cruise)
    tw_climb = (climb_rate / v_climb) + (q_climb * cd0_active / ws_crit_pa) + (k_factor * ws_crit_pa / q_climb)
    tw_turn = q_cruise * ((cd0_active / ws_crit_pa) + ws_crit_pa * k_factor * ((n_turn / q_cruise)**2))
    tw_to = ((v_to**2) / (2 * g * s_g)) + (q_to_avg * 0.05 / ws_crit_pa) + runway_friction * (1.0 - (q_to_avg * 0.6 / ws_crit_pa))

    # Convert to Electrical Power-to-Weight (W/kg)
    pw_crit = {
        "Cruise": (tw_cruise * g * v_cruise) / (eta_cruise * eta_motor_esc),
        "Climb": (tw_climb * g * v_climb) / (eta_climb * eta_motor_esc),
        "Turn": (tw_turn * g * v_cruise) / (eta_cruise * eta_motor_esc),
        "Takeoff Ground Run": (tw_to * g * v_to_avg) / (eta_to * eta_motor_esc)
    }

    governing_phase = max(pw_crit, key=pw_crit.get)
    opt_pw = pw_crit[governing_phase]
    s_req = target_mtow / opt_ws_kgm2
    total_power_req_w = target_mtow * opt_pw

    st.metric("Optimal Wing Loading (W/S)", f"{opt_ws_kgm2:.2f} kg/m^2")
    st.metric("Governing Sizing Phase", governing_phase)
    st.metric("Minimum Electrical P/W", f"{opt_pw:.1f} W/kg")
    st.metric("Required Electrical Power", f"{total_power_req_w:.1f} W")
    st.metric("Required Wing Area (S)", f"{s_req:.3f} m^2")

with col_plot:
    ws_pa_range = np.linspace(1.0, max_ws_plot * g, 300)
    ws_kgm2_range = ws_pa_range / g

    tw_c = (q_cruise * cd0_active / ws_pa_range) + (k_factor * ws_pa_range / q_cruise)
    tw_cl = (climb_rate / v_climb) + (q_climb * cd0_active / ws_pa_range) + (k_factor * ws_pa_range / q_climb)
    tw_t = q_cruise * ((cd0_active / ws_pa_range) + ws_pa_range * k_factor * ((n_turn / q_cruise)**2))
    tw_to_curve = ((v_to**2) / (2 * g * s_g)) + (q_to_avg * 0.05 / ws_pa_range) + runway_friction * (1.0 - (q_to_avg * 0.6 / ws_pa_range))

    pw_c = (tw_c * g * v_cruise) / (eta_cruise * eta_motor_esc)
    pw_cl = (tw_cl * g * v_climb) / (eta_climb * eta_motor_esc)
    pw_t = (tw_t * g * v_cruise) / (eta_cruise * eta_motor_esc)
    pw_to_curve = (tw_to_curve * g * v_to_avg) / (eta_to * eta_motor_esc)

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=100)
    ax.plot(ws_kgm2_range, pw_c, label="Cruise", color="crimson")
    ax.plot(ws_kgm2_range, pw_cl, label="Climb", color="royalblue")
    ax.plot(ws_kgm2_range, pw_t, label="Turn", color="purple")
    ax.plot(ws_kgm2_range, pw_to_curve, label="Takeoff", color="forestgreen")
    ax.axvline(x=opt_ws_kgm2, label="Stall Limit", color="black", linestyle="--", lw=1.5)
    ax.plot(opt_ws_kgm2, opt_pw, "ro", markersize=8, label=f"Design Point ({opt_ws_kgm2:.2f}, {opt_pw:.1f})")

    upper_pw = np.maximum.reduce([pw_c, pw_cl, pw_t, pw_to_curve])
    feasible_mask = ws_kgm2_range <= opt_ws_kgm2
    ax.fill_between(ws_kgm2_range[feasible_mask], upper_pw[feasible_mask], max_pw_plot, color="lightgray", alpha=0.4)

    ax.set_xlim(0, max_ws_plot)
    ax.set_ylim(0, max_pw_plot)
    ax.set_xlabel("Wing Loading W/S (kg/m^2)")
    ax.set_ylabel("Electrical Power Loading P/W (W/kg)")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="upper right", fontsize=8.0)
    st.pyplot(fig)

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

with col_w2:
    st.write("Calculated Metrics:")
    sm1, sm2 = st.columns(2)
    sm1.metric("Wingspan (b)", f"{wingspan_total:.2f} m")
    sm1.metric("Mean Aero Chord (MAC)", f"{mac*100:.1f} cm")
    sm2.metric("Wing Aerodynamic Center", f"{x_ac_wing:.1f} mm")
    sm2.metric("Calculated Static Margin", f"{static_margin:.1f}% MAC")

    if static_margin < sm_min:
        st.error(f"Stability Hazard: Static margin is {static_margin:.1f}%, below set limit of {sm_min:.1f}%. Move mass forward or wing aft.")
    elif static_margin > sm_max:
        st.warning(f"Over-Stable / High Trim Drag: Static margin is {static_margin:.1f}%, above set limit of {sm_max:.1f}%. Move mass aft or wing forward.")
    else:
        st.success(f"Stability Acceptable: Static margin is inside bounds ({sm_min:.1f}% - {sm_max:.1f}%).")

# -------------------------------------------------------------
# STAGE 4: GUST LOAD FACTOR VERIFICATION (FAR 23 / CS-VLA)
# -------------------------------------------------------------
st.header("4. Gust Load Factor & Atmospheric Safety Check")
g_col1, g_col2 = st.columns(2)

with g_col1:
    gust_v = st.number_input("Design Vertical Gust Velocity U_de (m/s)", value=7.0, step=0.5, key="gust_velocity")
    n_limit_pos = st.number_input("Design Positive Structural Limit Load Factor (+n_limit)", value=3.8, step=0.1, key="n_pos_limit")
    
    # 3D Lift Curve Slope calculation (rad^-1) using Helmbold / Diederich formula
    cla_2d = 2.0 * math.pi
    cla_3d = cla_2d / (math.sqrt(1.0 + (cla_2d / (math.pi * ar))**2) + (cla_2d / (math.pi * ar)))
    
    # Wing loading in Pascals
    ws_actual_pa = (target_mtow * g) / s_req
    
    # Aircraft mass ratio mu_g
    mu_g = (2.0 * ws_actual_pa) / (rho * mac * cla_3d * g)
    
    # Pratt gust alleviation factor Kg
    k_g = (0.88 * mu_g) / (5.3 + mu_g)
    
    # Incremental gust load factor delta_n
    delta_n_gust = (k_g * cla_3d * rho * v_cruise * gust_v) / (2.0 * ws_actual_pa)
    n_peak_gust = 1.0 + delta_n_gust

with g_col2:
    st.subheader(f"Gust Response at {gust_v:.1f} m/s")
    g_m1, g_m2 = st.columns(2)
    g_m1.metric("Gust Alleviation Factor (Kg)", f"{k_g:.3f}")
    g_m1.metric("3D Lift Curve Slope (C_L_alpha)", f"{cla_3d:.2f} /rad")
    g_m2.metric("Gust Induced Delta n", f"+{delta_n_gust:.2f} g")
    g_m2.metric("Total Peak Load Factor", f"{n_peak_gust:.2f} g")

    if n_peak_gust > n_limit_pos:
        st.error(
            f"**Structural Hazard:** Peak gust load ({n_peak_gust:.2f} g) exceeds the allowable limit factor "
            f"({n_limit_pos:.2f} g). Structural failure or wing deformation risk under {gust_v} m/s vertical gust. "
            f"Increase design wing loading (W/S), reduce cruise speed, or beef up the spar."
        )
    else:
        structural_margin = ((n_limit_pos - n_peak_gust) / n_limit_pos) * 100.0
        st.success(
            f"**Safe Against Gusts:** Peak load ({n_peak_gust:.2f} g) is below the structural limit ({n_limit_pos:.2f} g). "
            f"Structural safety margin: {structural_margin:.1f}%."
        )

# -------------------------------------------------------------
# STAGE 5: AIRFOIL TRANSLATION
# -------------------------------------------------------------
st.header("5. Airfoil 2D Lift Requirements")
cl_3d_cruise = (2.0 * target_mtow * g) / (rho * (v_cruise**2) * s_req)
cl_2d_cruise = cl_3d_cruise / (1.0 - (cl_3d_cruise / (math.pi * ar * e0)))
cl_2d_stall = cl_max_est / (1.0 - (cl_max_est / (math.pi * ar * e0)))

af1, af2, af3 = st.columns(3)
af1.metric("Target 3D Cruise CL", f"{cl_3d_cruise:.3f}")
af2.metric("Target 2D Airfoil Cl (Cruise)", f"{cl_2d_cruise:.3f}")
af3.metric("Target 2D Airfoil Cl_max", f"{cl_2d_stall:.3f}")

# -------------------------------------------------------------
# STAGE 6: TAIL SIZING
# -------------------------------------------------------------
st.header("6. Empennage Sizing")
tl1, tl2 = st.columns(2)

with tl1:
    st.subheader("Horizontal Tail")
    l_h = st.slider("Horizontal Moment Arm l_H (m)", 0.40, 1.20, 0.65, step=0.05)
    v_h = st.slider("Volume Coefficient V_H", 0.40, 0.85, 0.60, step=0.05)
    s_h = (v_h * s_req * mac) / l_h
    st.metric("Horizontal Tail Area (S_H)", f"{s_h:.3f} m^2")

with tl2:
    st.subheader("Vertical Tail")
    l_v = st.slider("Vertical Moment Arm l_V (m)", 0.40, 1.20, 0.65, step=0.05)
    v_v = st.slider("Volume Coefficient V_V", 0.02, 0.06, 0.04, step=0.005)
    s_v = (v_v * s_req * wingspan_total) / l_v
    st.metric("Vertical Fin Area (S_V)", f"{s_v:.3f} m^2")

# -------------------------------------------------------------
# STAGE 7: DRAG BUILDUP FEEDBACK
# -------------------------------------------------------------
st.header("7. Drag Buildup Verification")
dg1, dg2 = st.columns(2)

with dg1:
    fuse_len = st.number_input("Fuselage Length (m)", value=0.90, step=0.05)
    fuse_dia = st.number_input("Fuselage Equivalent Diameter (m)", value=0.12, step=0.01)
    s_wet_fuse = math.pi * fuse_dia * fuse_len * 0.8
    cf_fuse = st.number_input("Skin Friction Cf", value=0.005, format="%.4f")
    cd_wing_emp = st.number_input("Wing & Tail Profile Drag", value=0.012, format="%.4f")
    gear_cd = st.selectbox("Landing Gear Drag", [0.000, 0.008, 0.015], index=1)

    fineness = fuse_len / fuse_dia
    form_factor = 1.0 + (60.0 / (fineness**3)) + (fineness / 400.0)
    cd0_fuse = (cf_fuse * form_factor * s_wet_fuse) / s_req
    total_recalc_cd0 = cd0_fuse + cd_wing_emp + gear_cd

with dg2:
    st.metric("Recalculated CD0", f"{total_recalc_cd0:.4f}")
    if abs(total_recalc_cd0 - st.session_state.cd0_active) > 0.003:
        st.warning(f"Drag mismatch: Initial {st.session_state.cd0_active:.4f} vs Buildup {total_recalc_cd0:.4f}")
    else:
        st.success("Drag assumption aligns with geometry.")

st.session_state.temp_recalc_cd0 = float(total_recalc_cd0)
st.button("Update Stage 1 Drag Assumption and Recalculate", on_click=update_cd0_callback)
